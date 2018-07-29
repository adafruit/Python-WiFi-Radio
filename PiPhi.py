#! /usr/bin/python

# UI wrapper for 'pianobar' client for Pandora, using Adafruit 16x2 LCD
# Pi Plate for Raspberry Pi.
# Written by Adafruit Industries.  MIT license.
#
# Required hardware includes any internet-connected Raspberry Pi
# system, any of the Adafruit 16x2 LCD w/Keypad Pi Plate varieties
# and either headphones or amplified speakers.
# Required software includes the Adafruit Raspberry Pi Python Code
# repository, pexpect library and pianobar.  A Pandora account is
# also necessary.
#
# Resources:
# http://www.adafruit.com/products/1109 RGB Positive 16x2 LCD + Keypad
# http://www.adafruit.com/products/1110 RGB Negative 16x2 LCD + Keypad
# http://www.adafruit.com/products/1115 Blue & White 16x2 LCD + Keypad

import atexit
import pexpect
import pickle
import socket
import time
import subprocess
import Adafruit_CharLCD
from Adafruit_CharLCD import Adafruit_CharLCDPlate

# Constants:
RGB_LCD = False  # Set to 'True' if using color backlit LCD
HALT_ON_EXIT = False  # Set to 'True' to shut down system when exiting
MAX_FPS = 6 if RGB_LCD else 4  # Limit screen refresh rate for legibility
VOL_MIN = -20
VOL_MAX = 15
VOL_DEFAULT = 0
HOLD_TIME = 3.0  # Time (seconds) to hold select button for shut down
PICKLEFILE = '/home/pi/.config/pianobar/state.p'

# Global state:
vol_cur = VOL_MIN  # Current volume
vol_new = VOL_DEFAULT  # 'Next' volume after interactions
vol_speed = 1.0  # Speed of volume change (accelerates w/hold)
vol_set = False  # True if currently setting volume
paused = False  # True if music is paused
sta_sel = False  # True if selecting station
vol_time = 0  # Time of last volume button interaction
play_msg_time = 0  # Time of last 'Playing' message display
sta_btn_time = 0  # Time of last button press on station menu
x_title = 16  # X position of song title (scrolling)
x_info = 16  # X position of artist/album (scrolling)
x_station = 0  # X position of station (scrolling)
x_title_wrap = 0
x_info_wrap = 0
x_station_wrap = 0
song_title = ''
song_info = ''
station_num = 0  # Station currently playing
station_new = 0  # Station currently highlighted in menu
station_list = ['']
station_ids = ['']

# Char 7 gets reloaded for different modes.  These are the bitmaps:
char_seven_bitmaps = [
  [0b10000,  # Play (also selected station)
   0b11000,
   0b11100,
   0b11110,
   0b11100,
   0b11000,
   0b10000,
   0b00000],
  [0b11011,  # Pause
   0b11011,
   0b11011,
   0b11011,
   0b11011,
   0b11011,
   0b11011,
   0b00000],
  [0b00000,  # Next Track
   0b10100,
   0b11010,
   0b11101,
   0b11010,
   0b10100,
   0b00000,
   0b00000]]


# --------------------------------------------------------------------------


# Exit handler tries to leave LCD in a nice state.
def clean_exit():
    if lcd is not None:
        time.sleep(0.5)
        lcd.set_backlight(0.0)
        lcd.clear()
        lcd.enable_display(False)
    if pianobar is not None:
        pianobar.kill(0)


def shutdown():
    lcd.clear()
    if HALT_ON_EXIT:
        if RGB_LCD: lcd.set_color(1.0, 1.0, 0)
        lcd.message('Wait 30 seconds\nto unplug...')
        # Ramp down volume over 5 seconds while 'wait' message shows
        steps = int((vol_cur - VOL_MIN) + 0.5) + 1
        pause = 5.0 / steps
        for i in range(steps):
            pianobar.send('(')
            time.sleep(pause)
        subprocess.call("sync")
        clean_exit()
        subprocess.call(["shutdown", "-h", "now"])
    else:
        exit(0)


# Draws song title or artist/album marquee at given position.
# Returns new position to avoid global uglies.
def marquee(s, x, y, x_wrap):
    lcd.set_cursor(0, y)
    if x > 0: # Initially scrolls in from right edge
        lcd.message(' ' * x + s[0:16-x])
    else:     # Then scrolls w/wrap indefinitely
        lcd.message(s[-x:16-x])
        if x < x_wrap: return 0
    return x - 1


def draw_playing():
    lcd.create_char(7, char_seven_bitmaps[0])
    lcd.set_cursor(0, 1)
    lcd.message('\x07 Playing       ')
    return time.time()


def draw_paused():
    lcd.create_char(7, char_seven_bitmaps[1])
    lcd.set_cursor(0, 1)
    lcd.message('\x07 Paused        ')


def draw_next_track():
    lcd.create_char(7, char_seven_bitmaps[2])
    lcd.set_cursor(0, 1)
    lcd.message('\x07 Next track... ')


# Draw station menu (overwrites fulls screen to facilitate scrolling)
def draw_stations(station_new, list_top, x_station, sta_btn_time):
    last = len(station_list)
    if last > 2: last = 2  # Limit stations displayed
    ret = 0  # Default return value (for station scrolling)
    line = 0  # Line counter
    msg = ''  # Clear output string to start
    for s in station_list[list_top:list_top + 2]: # For each station...
        sLen = len(s)  # Length of station name
        if (list_top + line) == station_new: # Selected station?
            msg += chr(7)  # Show selection cursor
            if sLen > 15:  # Is station name longer than line?
                if (time.time() - sta_btn_time) < 0.5:
                    # Just show start of line for half a sec
                    s2 = s[0:15]
                else:
                    # After that, scrollinate
                    s2 = s + '   ' + s[0:15]
                    x_station_wrap = -(sLen + 2)
                    s2 = s2[-x_station:15 - x_station]
                    if x_station > x_station_wrap:
                        ret = x_station - 1
            else:  # Short station name - pad w/spaces if needed
                s2 = s[0:15]
                if sLen < 15: s2 += ' ' * (15 - sLen)
        else:  # Not currently-selected station
            msg += ' '  # No cursor
            s2 = s[0:15]  # Clip or pad name to 15 chars
            if sLen < 15:
                s2 += ' ' * (15 - sLen)
        msg += s2  # Add station name to output message
        line += 1
        if line == last:
            break
        msg += '\n'  # Not last line - add newline
    lcd.set_cursor(0, 0)
    lcd.message(msg)
    return ret


def get_stations():
    lcd.clear()
    lcd.message('Retrieving\nstation list...')
    pianobar.expect('Select station: ', timeout=20)
    # 'before' is now string of stations I believe
    # break up into separate lines
    a = pianobar.before.splitlines()
    a = [line.decode() for line in a]
    names = []
    ids = []
    # Parse each line
    for b in a[:-1]:  # Skip last line (station select prompt)
        # Occasionally a queued up 'TIME: -XX:XX/XX:XX' string or
        # 'new playlist...' appears in the output.  Station list
        # entries have a known format, so it's straightforward to
        # skip these bogus lines.
        # print '\"{}\"'.format(b)
        if (b.find('playlist...') >= 0) or (b.find('Autostart') >= 0):
            continue
        # if b[0:5].find(':') >= 0: continue
        # if (b.find(':') >= 0) or (len(b) < 13): continue
        # Alternate strategy: must contain either 'QuickMix' or 'Radio':
        # Somehow the 'playlist' case would get through this check.  Buh?
        if b.find('Radio') or b.find('QuickMix'):
            id = b[5:7].strip()
            name = b[13:].strip()
            # If 'QuickMix' found, always put at head of list
            if name == 'QuickMix':
                ids.insert(0, id)
                names.insert(0, name)
            else:
                ids.append(id)
                names.append(name)
    return names, ids


# --------------------------------------------------------------------------
# Initialization

atexit.register(clean_exit)

lcd = Adafruit_CharLCDPlate()
lcd.home()
lcd.clear()

# Create volume bargraph custom characters (chars 0-5):
for i in range(6):
    bitmap = []
    bits = (255 << (5 - i)) & 0x1f
    for j in range(8): bitmap.append(bits)
    lcd.create_char(i, bitmap)

# Create up/down icon (char 6)
lcd.create_char(6,
                [0b00100,
                 0b01110,
                 0b11111,
                 0b00000,
                 0b00000,
                 0b11111,
                 0b01110,
                 0b00100])

# By default, char 7 is loaded in 'pause' state
lcd.create_char(7, char_seven_bitmaps[1])

# Get last-used volume and station name from pickle file
try:
    f = open(PICKLEFILE, 'rb')
    v = pickle.load(f)
    f.close()
    vol_new = v[0]
    default_station = v[1]
except:
    default_station = None

# Show IP address (if network is available).  System might be freshly
# booted and not have an address yet, so keep trying for a couple minutes
# before reporting failure.
t = time.time()
while True:
    if (time.time() - t) > 120:
        # No connection reached after 2 minutes
        if RGB_LCD:
            lcd.set_color(1.0, 0, 0)
        lcd.message('Network is\nunreachable')
        time.sleep(30)
        exit(0)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 0))
        if RGB_LCD:
            lcd.set_color(0, 1.0, 0)
        else:
            lcd.set_backlight(1.0)
        lcd.message('My IP address is\n' + s.getsockname()[0])
        time.sleep(5)
        break  # Success -- let's hear some music!
    except:
        time.sleep(1)  # Pause a moment, keep trying

# Launch pianobar:
print('Spawning pianobar...')
pianobar = pexpect.spawn('pianobar')
print('Receiving station list...')
pianobar.expect('Get stations... Ok.\r\n', timeout=60)
station_list, station_ids = get_stations()
try:    # Use station name from last session
    station_num = station_list.index(default_station)
except: # Use first station in list
    station_num = 0
print('Selecting station ' + station_ids[station_num])
pianobar.sendline(station_ids[station_num])


# --------------------------------------------------------------------------
# Main loop.  This is not quite a straight-up state machine; there's some
# persnickety 'nesting' and canceling among mode states, so instead a few
# global booleans take care of it rather than a mode variable.

if RGB_LCD:
    lcd.set_backlight(1.0)
lastTime = 0

pattern_list = pianobar.compile_pattern_list(['SONG: ', 'STATION: ', 'TIME: '])

while pianobar.isalive():

    # Process all pending pianobar output
    while True:
        try:
            x = pianobar.expect(pattern_list, timeout=0)
            if x == 0:
                song_title = ''
                song_info = ''
                x_title = 16
                x_info = 16
                x_title_wrap = 0
                x_info_wrap = 0
                x = pianobar.expect(' \| ')
                if x == 0:  # Title | Artist | Album
                    print('Song: "{}"'.format(pianobar.before.decode()))
                    s = pianobar.before.decode() + '    '
                    n = len(s)
                    x_title_wrap = -n + 2
                    # 1+ copies + up to 15 chars for repeating scroll
                    song_title = s * int(1 + (16 / n)) + s[0:16]
                    x = pianobar.expect(' \| ')
                    if x == 0:
                        print('Artist: "{}"'.format(pianobar.before.decode()))
                        artist = pianobar.before.decode()
                        x = pianobar.expect('\r\n')
                        if x == 0:
                            print('Album: "{}"'.format(pianobar.before.decode()))
                            s = artist + ' < ' + pianobar.before.decode() + ' > '
                            n = len(s)
                            x_info_wrap = -n + 2
                            # 1+ copies + up to 15 chars for repeating scroll
                            song_info = s * int(2 + (16 / n)) + s[0:16]
            elif x == 1:
                x = pianobar.expect(' \| ')
                if x == 0:
                    print('Station: "{}"'.format(pianobar.before.decode()))
            elif x == 2:
                # Time doesn't include newline - prints over itself.
                x = pianobar.expect('\r', timeout=1)
                if x == 0:
                    print('Time: {}'.format(pianobar.before.decode()))
                # Periodically dump state (volume and station name)
                # to pickle file so it's remembered between each run.
                try:
                    f = open(PICKLEFILE, 'wb')
                    pickle.dump([vol_cur, station_list[station_num]], f)
                    f.close()
                except:
                    pass
        except pexpect.EOF:
            break
        except pexpect.TIMEOUT:
            break

    # Poll all buttons once, avoids repeated I2C traffic for different cases
    btn_up = lcd.is_pressed(Adafruit_CharLCD.UP)
    btn_down = lcd.is_pressed(Adafruit_CharLCD.DOWN)
    btn_left = lcd.is_pressed(Adafruit_CharLCD.LEFT)
    btn_right = lcd.is_pressed(Adafruit_CharLCD.RIGHT)
    btn_sel = lcd.is_pressed(Adafruit_CharLCD.SELECT)

    # Certain button actions occur regardless of current mode.
    # Holding the select button (for shutdown) is a big one.
    if btn_sel:

        t = time.time()  # Start time of button press
        while lcd.is_pressed(Adafruit_CharLCD.SELECT):  # Wait for button release
            if (time.time() - t) >= HOLD_TIME:  # Extended hold?
                shutdown()  # We're outta here
        # If tapped, different things in different modes...
        if sta_sel:  # In station select menu...
            pianobar.send('\n')  # Cancel station select
            sta_sel = False  # Cancel menu and return to
            if paused: draw_paused()  # play or paused state
        else:  # In play/pause state...
            vol_set = False  # Exit volume-setting mode (if there)
            paused = not paused  # Toggle play/pause
            pianobar.send('p')  # Toggle pianobar play/pause
            if paused:
                draw_paused()  # Display play/pause change
            else:
                play_msg_time = draw_playing()

    # Right button advances to next track in all modes, even paused,
    # when setting volume, in station menu, etc.
    elif btn_right:

        draw_next_track()
        if sta_sel:      # Cancel station select, if there
            pianobar.send('\n')
            sta_sel = False
        paused = False  # Un-pause, if there
        vol_set = False
        pianobar.send('n')

    # Left button enters station menu (if currently in play/pause state),
    # or selects the new station and returns.
    elif btn_left:

        sta_sel = not sta_sel  # Toggle station menu state
        if sta_sel:
            # Entering station selection menu.  Don't return to volume
            # select, regardless of outcome, just return to normal play.
            pianobar.send('s')
            lcd.create_char(7, char_seven_bitmaps[0])
            vol_set = False
            cursorY = 0  # Cursor position on screen
            station_new = 0  # Cursor position in list
            list_top = 0  # Top of list on screen
            x_station = 0  # X scrolling for long station names
            # Just keep the list we made at start-up
            # stationList, stationIDs = getStations()
            sta_btn_time = time.time()
            draw_stations(station_new, list_top, 0, sta_btn_time)
        else:
            # Just exited station menu with selection - go play.
            station_num = station_new # Make menu selection permanent
            print('Selecting station: "{}"'.format(station_ids[station_num]))
            pianobar.sendline(station_ids[station_num])
            paused = False

    # Up/down buttons either set volume (in play/pause) or select station
    elif btn_up or btn_down:

        if sta_sel:
            # Move up or down station menu
            if btn_down:
                if station_new < (len(station_list) - 1):
                    station_new += 1  # Next station
                    if cursorY < 1:
                        cursorY += 1  # Move cursor
                    else:
                        list_top += 1  # Y-scroll
                    x_station = 0  # Reset X-scroll
            elif station_new > 0:  # btnUp implied
                    station_new -= 1  # Prev station
                    if cursorY > 0:
                        cursorY -= 1  # Move cursor
                    else:
                        list_top -= 1  # Y-scroll
                    x_station = 0  # Reset X-scroll
            sta_btn_time = time.time()  # Reset button time
            x_station = draw_stations(station_new, list_top, 0, sta_btn_time)
        else:
            # Not in station menu
            if vol_set is False:
                # Just entering volume-setting mode; init display
                lcd.set_cursor(0, 1)
                vol_cur_i = int((vol_cur - VOL_MIN) + 0.5)
                n = int(vol_cur_i / 5)
                s = (chr(6) + ' Volume ' +
                     chr(5) * n +  # Solid brick(s)
                     chr(vol_cur_i % 5) +  # Fractional brick
                     chr(0) * (6 - n))  # Spaces
                lcd.message(s)
                vol_set = True
                vol_speed = 1.0
            # Volume-setting mode now active (or was already there);
            # act on button press.
            if btn_up:
                vol_new = vol_cur + vol_speed
                if vol_new > VOL_MAX: vol_new = VOL_MAX
            else:
                vol_new = vol_cur - vol_speed
                if vol_new < VOL_MIN:
                    vol_new = VOL_MIN
            vol_time = time.time()  # Time of last volume button press
            vol_speed *= 1.15  # Accelerate volume change

    # Other logic specific to unpressed buttons:
    else:
        if sta_sel:
            # In station menu, X-scroll active station name if long
            if len(station_list[station_new]) > 15:
                x_station = draw_stations(station_new, list_top, x_station,
                                          sta_btn_time)
        elif vol_set:
            vol_speed = 1.0 # Buttons released = reset volume speed
            # If no interaction in 4 seconds, return to prior state.
            # Volume bar will be erased by subsequent operations.
            if (time.time() - vol_time) >= 4:
                vol_set = False
                if paused: draw_paused()

    # Various 'always on' logic independent of buttons
    if not sta_sel:
        # Play/pause/volume: draw upper line (song title)
        if song_title is not None:
            x_title = marquee(song_title, x_title, 0, x_title_wrap)

        # Integerize current and new volume values
        vol_cur_i = int((vol_cur - VOL_MIN) + 0.5)
        vol_new_i = int((vol_new - VOL_MIN) + 0.5)
        vol_cur = vol_new
        # Issue change to pianobar
        if vol_cur_i != vol_new_i:
            d = vol_new_i - vol_cur_i
            if d > 0:
                s = ')' * d
            else:
                s = '(' * -d
            pianobar.send(s)

        # Draw lower line (volume or artist/album info):
        if vol_set:
            if vol_new_i != vol_cur_i:  # Draw only changes
                if vol_new_i > vol_cur_i:
                    x = int(vol_cur_i / 5)
                    n = int(vol_new_i / 5) - x
                    s = chr(5) * n + chr(vol_new_i % 5)
                else:
                    x = int(vol_new_i / 5)
                    n = int(vol_cur_i / 5) - x
                    s = chr(vol_new_i % 5) + chr(0) * n
                lcd.set_cursor(x + 9, 1)
                lcd.message(s)
        elif not paused:
            if (time.time() - play_msg_time) >= 3:
                # Display artist/album (rather than 'Playing')
                x_info = marquee(song_info, x_info, 1, x_info_wrap)

    # Throttle frame rate, keeps screen legible
    while True:
        t = time.time()
        if (t - lastTime) > (1.0 / MAX_FPS):
            break
    lastTime = t
