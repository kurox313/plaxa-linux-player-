#!/usr/bin/env python3
"""
________________/\\\\\\________________________________________________        
 _______________\////\\\________________________________________________       
  ___/\\\\\\\\\_____\/\\\________________________________________________      
   __/\\\/////\\\____\/\\\_____/\\\\\\\\\_____/\\\____/\\\__/\\\\\\\\\____     
    _\/\\\\\\\\\\_____\/\\\____\////////\\\___\///\\\/\\\/__\////////\\\___    
     _\/\\\//////______\/\\\______/\\\\\\\\\\____\///\\\/______/\\\\\\\\\\__   
      _\/\\\____________\/\\\_____/\\\/////\\\_____/\\\/\\\____/\\\/////\\\__  
       _\/\\\__________/\\\\\\\\\_\//\\\\\\\\/\\__/\\\/\///\\\_\//\\\\\\\\/\\_ 
        _\///__________\/////////___\////////\//__\///____\///___\////////\//__
        

Requirements:
    playerctl cava
   (python) syncedlyrics deep-translator

Controls:
    Space       Play / Pause
    ←  →        Prev / Next
    ↑  ↓        Volume +10% / -10%
    [  ]        Seek -5s / +5s
    Tab         Cycle players
    q / Ctrl+C  Quit
"""

import os, sys, time, math, random, threading
import re, shutil, signal, subprocess, tempfile
from dataclasses import dataclass
from typing import Optional, List
# For Windows users: this script is designed for Unix-like terminals and may not work properly on Windows' default console. Consider using WSL, Git Bash, or Windows Terminal.

# this project is made by Claude 4.6 but prompted by kuro (nothing._.) [discord]
# project is open-source under MIT license, see LICENSE.txt

# ── Configurations ─────────────────────────────────────────────────────────────
# meow :3
# ── Theme ──────────────────────────────────────────────────────────────────────

BG     = (18,  18,  20)
FG     = (220, 210, 195)
ACCENT = (220, 120,  30)
DIM    = ( 75,  68,  58)
BAR_LO = (140,  70,  10)
BAR_HI = (255, 155,  40)
GREEN  = ( 90, 200, 100)
TAB_BG = ( 35,  30,  25)

# ── Config ─────────────────────────────────────────────────────────────────────
ANIMATION_FPS  = 60
POLL_INTERVAL  = 0.5
MIN_BEAT_MS    = 300
MAX_BEAT_MS    = 2000
WORD_HOLD_FRAC = 0.72
CAVA_BARS      = 64
CAVA_BAR_ROWS  = 10
CAVA_MAX_VAL   = 1000
SLIDE_DURATION = 0.25

# ── Terminal helpers ───────────────────────────────────────────────────────────
ESC = "\033"
def csi(*a):    return f"{ESC}[{';'.join(str(x) for x in a)}"
def clear():    return csi() + "2J"
def hide_cur(): return csi() + "?25l"
def show_cur(): return csi() + "?25h"
def move(r, c): return csi(r, c) + "H"
def fg(r,g,b):  return csi(38,2,r,g,b) + "m"
def bg(r,g,b):  return csi(48,2,r,g,b) + "m"
def rst():      return csi() + "0m"
def bold():     return csi() + "1m"

def term_size():
    s = shutil.get_terminal_size((80, 24))
    return s.columns, s.lines

def write(s):
    sys.stdout.write(s); sys.stdout.flush()

def lerp(c1, c2, t):
    return tuple(int(c1[i]*(1-t) + c2[i]*t) for i in range(3))

# ── Optional deps ──────────────────────────────────────────────────────────────
def _try(pkg):
    import importlib
    try: return importlib.import_module(pkg)
    except ImportError: return None

_syncedlyrics  = _try("syncedlyrics")
_deep_translator = _try("deep_translator")

def _translate_lines(lines, target_lang="en"):
    """Translate a list of (time, text) pairs. Returns list of translated strings or None."""
    if not lines:
        return None
    texts = [t for _, t in lines]
    try:
        if _deep_translator:
            from deep_translator import GoogleTranslator
            tr = GoogleTranslator(source='auto', target=target_lang)
            # batch translate — up to 5000 chars per call
            batch, results, i = [], [], 0
            while i < len(texts):
                batch.append(texts[i]); i += 1
                joined = "\n".join(batch)
                if len(joined) > 4500 or i == len(texts):
                    translated = tr.translate(joined)
                    results.extend(translated.split("\n") if translated else batch)
                    batch = []
            return results if len(results) == len(texts) else None
    except Exception:
        pass
    return None

# ── playerctl ──────────────────────────────────────────────────────────────────
def _pc(*args, player=None) -> str:
    cmd = ['playerctl']
    if player: cmd += ['-p', player]
    cmd += list(args)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1.5)
        return r.stdout.strip()
    except: return ""

def list_players() -> List[str]:
    return [p for p in _pc('-l').splitlines() if p.strip()]

@dataclass
class TrackInfo:
    player:   str   = ""
    title:    str   = ""
    artist:   str   = ""
    album:    str   = ""
    status:   str   = ""
    position: float = 0.0
    duration: float = 0.0
    volume:   float = 1.0

def poll_player(player: str) -> TrackInfo:
    def get(fmt): return _pc('metadata', '--format', fmt, player=player)
    status = _pc('status', player=player)
    title  = get('{{title}}')
    artist = get('{{artist}}')
    album  = get('{{album}}')
    try:    pos = float(_pc('position', player=player))
    except: pos = 0.0
    try:    dur = float(get('{{mpris:length}}')) / 1_000_000
    except: dur = 0.0
    try:    vol = float(_pc('volume', player=player))
    except: vol = 1.0
    return TrackInfo(player=player, title=title, artist=artist, album=album,
                     status=status, position=pos, duration=dur, volume=vol)

# ── CAVA ───────────────────────────────────────────────────────────────────────
_CAVA_CFG = """
[general]
bars = {bars}
framerate = 60
sensitivity = 100
lower_cutoff_freq = 50
higher_cutoff_freq = 10000

[input]
method = pipewire
source = {source}

[output]
method = raw
raw_target = /dev/stdout
data_format = ascii
ascii_max_range = {max_val}
bar_delimiter = 59
frame_delimiter = 10
"""

def _pw_source_for_player(player_name: str) -> str:
    """Return pipewire monitor source for the player's audio, or 'auto'."""
    import json
    proc_name = player_name.split('.')[0].lower()
    try:
        r = subprocess.run(['pgrep', '-x', proc_name],
                           capture_output=True, text=True, timeout=2)
        if not r.stdout.strip():
            r = subprocess.run(['pgrep', proc_name],
                               capture_output=True, text=True, timeout=2)
        pids = set(r.stdout.strip().split())
        if not pids: return "auto"
    except: return "auto"
    try:
        r = subprocess.run(['pw-dump'], capture_output=True, text=True, timeout=3)
        nodes = json.loads(r.stdout)
    except: return "auto"
    by_id = {n.get('id'): n for n in nodes if isinstance(n, dict)}
    stream_ids = set()
    for node in nodes:
        if not isinstance(node, dict): continue
        props = node.get('info', {}).get('props', {})
        pid   = str(props.get('application.process.id', ''))
        ntype = props.get('media.class', '')
        if pid in pids and 'Stream' in ntype and 'Output' in ntype:
            stream_ids.add(node.get('id'))
    if not stream_ids: return "auto"
    sink_ids = set()
    for node in nodes:
        if not isinstance(node, dict): continue
        if node.get('type') != 'PipeWire:Interface:Link': continue
        info = node.get('info', {})
        if info.get('output-node-id') in stream_ids:
            sink_ids.add(info.get('input-node-id'))
    for sid in sink_ids:
        props  = by_id.get(sid, {}).get('info', {}).get('props', {})
        name   = props.get('node.name', '')
        mclass = props.get('media.class', '')
        if 'Sink' in mclass and name:
            return name + '.monitor'
    return "auto"

class CavaReader:
    def __init__(self):
        self._bars     = CAVA_BARS
        self.values    = [0] * CAVA_BARS
        self._smoothed = [0.0] * CAVA_BARS
        self._proc     = None
        self._running  = False
        self._lock     = threading.Lock()
        self._cfgfile  = None

    def _spawn(self, source: str = "auto"):
        self._stop_proc()
        self._source   = source
        self.values    = [0] * CAVA_BARS
        self._smoothed = [0.0] * CAVA_BARS
        cfg = _CAVA_CFG.format(bars=CAVA_BARS, max_val=CAVA_MAX_VAL, source=source)
        self._cfgfile = tempfile.NamedTemporaryFile(
            mode='w', suffix='.ini', delete=False, prefix='mc_cava_')
        self._cfgfile.write(cfg); self._cfgfile.flush(); self._cfgfile.close()
        try:
            self._proc = subprocess.Popen(
                ['cava', '-p', self._cfgfile.name],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
            threading.Thread(target=self._read, daemon=True).start()
        except FileNotFoundError:
            pass

    def start(self, source: str = "auto"):
        self._running = True
        self._spawn(source)

    def set_source(self, source: str):
        self._spawn(source)

    def _read(self):
        buf  = b''
        proc = self._proc
        while self._running and proc and proc == self._proc:
            try:
                chunk = proc.stdout.read(256)
                if not chunk: break
                buf += chunk
                while b'\n' in buf:
                    line, buf = buf.split(b'\n', 1)
                    parts = [p for p in line.split(b';') if p.strip().isdigit()]
                    if len(parts) >= CAVA_BARS:
                        with self._lock:
                            self.values = [int(p) for p in parts[:CAVA_BARS]]
            except: break

    def get(self, smooth=0.75) -> list:
        with self._lock: raw = list(self.values)
        for i in range(CAVA_BARS):
            self._smoothed[i] = self._smoothed[i]*smooth + raw[i]*(1-smooth)
        return list(self._smoothed)

    def _stop_proc(self):
        if self._proc:
            try: self._proc.terminate()
            except: pass
            self._proc = None
        if self._cfgfile:
            try: os.unlink(self._cfgfile.name)
            except: pass

    def stop(self):
        self._running = False
        self._stop_proc()

# ── Lyrics ─────────────────────────────────────────────────────────────────────
def _parse_lrc(text: str):
    pat = re.compile(r'\[(\d+):(\d+\.\d+)\](.*)')
    out = []
    for line in text.splitlines():
        m = pat.match(line.strip())
        if m:
            mins, secs, txt = m.groups()
            t = int(mins)*60 + float(secs)
            txt = txt.strip()
            if txt: out.append((t, txt))
    return sorted(out, key=lambda x: x[0])

def _fetch_lyrics(title: str, artist: str):
    if not _syncedlyrics: return None
    import logging
    logging.disable(logging.CRITICAL)
    try:    return _syncedlyrics.search(f"{title} {artist}")
    except: return None
    finally: logging.disable(logging.NOTSET)

# ── Lyrics Display ────────────────────────────────────────────────────────────

# ── Drawing ────────────────────────────────────────────────────────────────────
def _draw_bg(tw: int, th: int):
    buf=[]
    for r in range(1,th+1): buf.append(move(r,1)+bg(*BG)+' '*tw)
    write("".join(buf)+rst())

def _draw_topbar(info: TrackInfo, players: List[str], tw: int):
    buf=[move(1,1)]; col=1
    for p in players:
        short=p.split('.')[-1][:12]; active=(p==info.player); label=f" {short} "
        if active: buf+=[bg(*ACCENT),fg(*BG),bold(),label,rst()]
        else:      buf+=[bg(*TAB_BG),fg(*DIM),label,rst()]
        col+=len(label)
    buf+=[bg(*BG),' ']; col+=1
    icon={'Playing':'▶','Paused':'⏸','Stopped':'■'}.get(info.status,'—')
    icol=GREEN if info.status=='Playing' else (ACCENT if info.status=='Paused' else DIM)
    buf+=[bg(*BG),fg(*icol),bold(),f'{icon} ',rst(),bg(*BG)]; col+=2
    if info.title:
        title=info.title[:max(0,tw-col-20)]
        buf+=[fg(*FG),bold(),title,rst(),bg(*BG)]; col+=len(title)
    if info.artist:
        artist=f"  {info.artist}"[:max(0,tw-col-10)]
        buf+=[fg(*DIM),artist,rst(),bg(*BG)]; col+=len(artist)
    vol_str=f" vol {int(info.volume*100)}% "
    buf+=[' '*max(0,tw-col-len(vol_str)),fg(*DIM),vol_str,rst()]
    write("".join(buf))

def _draw_progress(info: TrackInfo, tw: int):
    dur=max(info.duration,0.001); pos=max(0.0,min(info.position,dur)); frac=pos/dur
    def fmt(s): s=int(max(0,s)); return f"{s//60}:{s%60:02d}"
    tstr=f" {fmt(pos)} / {fmt(dur)} "; bw=tw-len(tstr)-2; filled=int(bw*frac)
    bar=fg(*ACCENT)+'━'*filled+fg(*DIM)+'─'*(bw-filled)
    write(move(2,1)+bg(*BG)+' '+bar+rst()+bg(*BG)+fg(*DIM)+tstr+rst())

def _draw_cava(bars: list, tw: int, th: int):
    if not bars: return
    n=len(bars); vr=CAVA_BAR_ROWS*2; buf=[]
    for ro in range(CAVA_BAR_ROWS):
        row=th-CAVA_BAR_ROWS+ro
        if row<2: continue
        vlo=(CAVA_BAR_ROWS-1-ro)*2; vhi=vlo+1
        buf.append(move(row,1)); line=[]
        for col in range(tw):
            idx=min(int(col*n/tw),n-1); val=min(1.0,bars[idx]/CAVA_MAX_VAL); fill=val*vr
            if fill>vhi:
                c=lerp(BAR_LO,BAR_HI,val); line.append(bg(*c)+' ')
            elif fill>vlo:
                c=lerp(BAR_LO,BAR_HI,val); line.append(fg(*c)+bg(*BG)+'▄')
            else:
                line.append(bg(*BG)+' ')
        buf.append("".join(line)+rst())
    write("".join(buf))

def _draw_karaoke(lines, prog_sec: float, tw: int, th: int, slide: float):
    cur=0
    for i,(t,_) in enumerate(lines):
        if t<=prog_sec: cur=i
    top_row=3; bot_row=th-CAVA_BAR_ROWS-1; mid_row=(top_row+bot_row)//2; half=(bot_row-top_row)//2
    buf=[]
    for r in range(top_row,bot_row+1): buf+=[move(r,1),bg(*BG),' '*tw]
    for offset in range(-half-1,half+2):
        idx=cur+offset; row=mid_row+offset+int(round(slide))
        if row<top_row or row>bot_row: continue
        buf+=[move(row,1),bg(*BG),' '*tw]
        if idx<0 or idx>=len(lines): continue
        _,txt=lines[idx]; text=txt.strip()
        edge=1.0-min(1.0,abs(slide)*abs(offset)*0.15)
        if offset==0:
            content=">> "+text
            if len(content)>tw-2: content=content[:tw-2]
            col=max(1,(tw-len(content))//2)
            blend=1.0-abs(slide)*0.6
            bglow=tuple(int(ACCENT[i]*blend+BG[i]*(1-blend)) for i in range(3))
            bfg  =tuple(int(BG[i]*blend+ACCENT[i]*(1-blend)) for i in range(3))
            buf+=[move(row,1),bg(*bglow),' '*tw,move(row,col),bg(*bglow),fg(*bfg),bold(),content,rst()]
        elif abs(offset)<=1:
            if len(text)>tw-4: text=text[:tw-4]
            col=max(1,(tw-len(text))//2); a=edge
            dm=tuple(int(FG[i]*a+BG[i]*(1-a)) for i in range(3))
            buf+=[move(row,col),fg(*dm),text,rst()]
        else:
            a=max(0.0,edge*(1.0-abs(offset)*0.18))
            dm=tuple(int(FG[i]*a+BG[i]*(1-a)) for i in range(3))
            if len(text)>tw-4: text=text[:tw-4]
            col=max(1,(tw-len(text))//2)
            buf+=[move(row,col),fg(*dm),text,rst()]
    write("".join(buf))

def _draw_karaoke_split(lines, translated, prog_sec: float, tw: int, th: int, slide: float):
    """Side-by-side karaoke: original left, translation right."""
    cur = 0
    for i,(t,_) in enumerate(lines):
        if t <= prog_sec: cur = i
    top_row = 3; bot_row = th - CAVA_BAR_ROWS - 1
    mid_row = (top_row + bot_row) // 2; half = (bot_row - top_row) // 2
    # Divider at center
    div_col  = tw // 2
    col_w    = div_col - 2   # usable width per side
    buf = []
    # Clear area + draw divider
    for r in range(top_row, bot_row + 1):
        buf += [move(r, 1), bg(*BG), ' '*tw]
        buf += [move(r, div_col), bg(*BG), fg(*DIM), '│', rst()]
    for offset in range(-half-1, half+2):
        idx = cur + offset
        row = mid_row + offset + int(round(slide))
        if row < top_row or row > bot_row: continue
        # Clear both halves
        buf += [move(row, 1), bg(*BG), ' '*(div_col-1)]
        buf += [move(row, div_col+1), bg(*BG), ' '*(tw-div_col)]
        buf += [move(row, div_col), bg(*BG), fg(*DIM), '│', rst()]
        if idx < 0 or idx >= len(lines): continue
        _, orig_text = lines[idx]
        orig_text = orig_text.strip()
        trans_text = (translated[idx] if translated and idx < len(translated) else "…").strip()
        edge = 1.0 - min(1.0, abs(slide)*abs(offset)*0.15)
        if offset == 0:
            blend = 1.0 - abs(slide)*0.6
            bglow = tuple(int(ACCENT[i]*blend + BG[i]*(1-blend)) for i in range(3))
            bfg   = tuple(int(BG[i]*blend + ACCENT[i]*(1-blend)) for i in range(3))
            # Left — original
            lo = orig_text[:col_w-3]
            lc = ">> " + lo
            lcol = max(1, (div_col - len(lc)) // 2)
            buf += [move(row, 1), bg(*bglow), ' '*(div_col-1),
                    move(row, lcol), bg(*bglow), fg(*bfg), bold(), lc, rst()]
            # Right — translation
            ro2 = trans_text[:col_w-3]
            rc  = ">> " + ro2
            rcol = div_col + 1 + max(0, (col_w - len(rc)) // 2)
            buf += [move(row, div_col+1), bg(*bglow), ' '*(tw-div_col),
                    move(row, rcol), bg(*bglow), fg(*bfg), bold(), rc, rst()]
            buf += [move(row, div_col), bg(*BG), fg(*DIM), '│', rst()]
        else:
            a = max(0.0, edge*(1.0 - abs(offset)*0.18)) if abs(offset) > 1 else edge
            dm = tuple(int(FG[i]*a + BG[i]*(1-a)) for i in range(3))
            # Left
            lo = orig_text[:col_w]
            lcol = max(1, (div_col - len(lo)) // 2)
            buf += [move(row, lcol), fg(*dm), lo, rst()]
            # Right
            ro2 = trans_text[:col_w]
            rcol = div_col + 1 + max(0, (col_w - len(ro2)) // 2)
            buf += [move(row, rcol), fg(*dm), ro2, rst()]
    write("".join(buf))

def _draw_no_players(tw: int, th: int):
    mid=(3+th-CAVA_BAR_ROWS-2)//2
    for i,m in enumerate(["No media players detected.",
                           "Start Spotify, MPV, VLC… and it'll appear here."]):
        col=max(1,(tw-len(m))//2)
        write(move(mid-1+i,col)+bg(*BG)+fg(*(FG if i==0 else DIM))+(bold() if i==0 else '')+m+rst())

def _draw_controls(tw: int, th: int):
    hint=" [Space] ▶⏸  [←][→] ⏮⏭  [↑][↓] Vol  [][  ] Seek  [Tab] Player  [L] Lyrics  [T] Translate  [q] Quit "
    write(move(th,1)+bg(*BG)+fg(*DIM)+hint[:tw].center(tw)+rst())

# ── Main controller ────────────────────────────────────────────────────────────

class MediaController:
    def __init__(self):
        self.cava          = CavaReader()
        self._running      = False
        self._resize       = False
        self._lock         = threading.Lock()
        self._players      : List[str] = []
        self._player_idx   : int = 0
        self._info         = TrackInfo()
        self._old_term     = None
        self._lyrics       : list = []
        self._last_tid     : str  = ""
        self._last_line    : int  = -1
        self._slide        : float = 0.0
        self._prog_sync    : Optional[tuple] = None

        self._cava_source  : str = "auto"
        self._active_player: str = ""
        self._lyrics_enabled : bool = False
        self._translate_enabled: bool = False
        self._translated     : list = []   # list of translated lines corresponding to self._lyrics

    def _poll_loop(self):
        while self._running:
            players=list_players()
            _player_changed=False; active_player=""
            with self._lock:
                self._players=players
                if not players:
                    self._info=TrackInfo(); self._prog_sync=None
                else:
                    self._player_idx=min(self._player_idx,len(players)-1)
                    active_player=players[self._player_idx]
                    info=poll_player(active_player)
                    mono_now=time.monotonic(); prog_sec=info.position
                    _player_changed=(active_player!=self._active_player)
                    self._active_player=active_player
                    if info.title!=self._last_tid:
                        self._last_tid=info.title; self._last_line=-1; self._slide=0.0
                        threading.Thread(target=self._reload_track,
                                         args=(info,prog_sec,mono_now),daemon=True).start()
                    if info.status in('Playing','Paused'):
                        self._prog_sync=(mono_now,prog_sec)
                    if (info.title==self._info.title and info.status=='Playing' and
                            abs(info.position-self._info.position)<3.0):
                        info.position=self._info.position
                    self._info=info
            if _player_changed:
                src=_pw_source_for_player(active_player) if active_player else "auto"
                self._cava_source=src; self.cava.set_source(src)
            time.sleep(POLL_INTERVAL)

    def _reload_track(self, info: TrackInfo, prog_sec: float, mono_now: float):
        lrc=_fetch_lyrics(info.title,info.artist)
        lines=_parse_lrc(lrc) if lrc else []
        with self._lock:
            self._lyrics=lines
            self._translated=[]
        tw,th=term_size(); _draw_bg(tw,th)
        # Fetch translation in background
        if lines:
            threading.Thread(target=self._fetch_translation, args=(lines,), daemon=True).start()

    def _fetch_translation(self, lines):
        result = _translate_lines(lines)
        if result:
            with self._lock:
                self._translated = result

    def _prog_now(self, now: float) -> float:
        with self._lock: sync=self._prog_sync; status=self._info.status
        if not sync: return 0.0
        lm,lp=sync
        return lp+(now-lm) if status=='Playing' else lp

    def _key_loop(self):
        import termios,tty
        fd=sys.stdin.fileno(); self._old_term=termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            while self._running:
                ch=sys.stdin.read(1)
                if not ch: continue
                if ch in('q','\x03'): self._running=False
                elif ch==' ':  self._cmd('play-pause')
                elif ch=='\t':
                    with self._lock:
                        n=len(self._players)
                        if n>1: self._player_idx=(self._player_idx+1)%n
                elif ch=='\x1b':
                    rest=sys.stdin.read(2)
                    if   rest=='[C': self._cmd('next')
                    elif rest=='[D': self._cmd('previous')
                    elif rest=='[A': self._vol(+0.1)
                    elif rest=='[B': self._vol(-0.1)
                elif ch=='[': self._seek(-5)
                elif ch==']': self._seek(+5)
                elif ch in('l','L'):
                    with self._lock:
                        self._lyrics_enabled = not self._lyrics_enabled
                elif ch in('t','T'):
                    with self._lock:
                        self._translate_enabled = not self._translate_enabled
        except: pass

    def _cmd(self,action):
        with self._lock: p=self._players[self._player_idx] if self._players else None
        if p: _pc(action,player=p)

    def _vol(self,delta):
        with self._lock:
            p=self._players[self._player_idx] if self._players else None; vol=self._info.volume
        if not p: return
        nv=max(0.0,min(1.0,vol+delta)); _pc('volume',str(round(nv,2)),player=p)
        with self._lock: self._info.volume=nv

    def _seek(self,delta):
        with self._lock: p=self._players[self._player_idx] if self._players else None
        if not p: return
        _pc('position',f'{abs(delta)}{("+" if delta>=0 else "-")}',player=p)

    def run(self):
        signal.signal(signal.SIGINT,  lambda *_: setattr(self,'_running',False))
        signal.signal(signal.SIGTERM, lambda *_: setattr(self,'_running',False))
        signal.signal(signal.SIGWINCH,lambda *_: setattr(self,'_resize',True))
        self._running=True
        self.cava.start(self._cava_source)
        threading.Thread(target=self._poll_loop,daemon=True).start()
        threading.Thread(target=self._key_loop, daemon=True).start()
        write(hide_cur()+clear())
        tw,th=term_size(); _draw_bg(tw,th)
        frame_time=1.0/ANIMATION_FPS; last_frame=time.monotonic()
        while self._running:
            now=time.monotonic(); dt=now-last_frame; last_frame=now
            if self._resize:
                self._resize=False; tw,th=term_size()
                write(clear()); _draw_bg(tw,th); self._last_line=-1
            else:
                tw,th=term_size()
            with self._lock:
                info=TrackInfo(**vars(self._info)); lyrics=list(self._lyrics); players=list(self._players)
                lyrics_on   = self._lyrics_enabled
                trans_on    = self._translate_enabled
                translated  = list(self._translated)
            prog_sec=self._prog_now(now)
            if info.status=='Playing' and info.duration>0:
                info.position=min(info.duration,prog_sec)
                with self._lock: self._info.position=info.position
            _draw_cava(self.cava.get(),tw,th)
            _draw_topbar(info,players,tw)
            _draw_progress(info,tw)
            if not players:
                _draw_no_players(tw,th)
            elif lyrics and lyrics_on and info.status in('Playing','Paused'):
                cur=0
                for i,(t,_) in enumerate(lyrics):
                    if t<=prog_sec: cur=i
                if cur!=self._last_line and self._last_line!=-1: self._slide=1.0
                if self._last_line==-1: _draw_bg(tw,th); _draw_topbar(info,players,tw); _draw_progress(info,tw)
                self._last_line=cur
                if self._slide>0.0:
                    self._slide=max(0.0,self._slide-dt/SLIDE_DURATION)
                    vis=self._slide**2*(3-2*self._slide)
                else: vis=0.0
                if trans_on:
                    _draw_karaoke_split(lyrics, translated, prog_sec, tw, th, vis)
                else:
                    _draw_karaoke(lyrics, prog_sec, tw, th, vis)
            elif info.status in('Playing','Paused'):
                top=3; bot=th-CAVA_BAR_ROWS-2; mid=(top+bot)//2
                for r in range(top,bot+1): write(move(r,1)+bg(*BG)+' '*tw)
                if info.title:
                    t=info.title[:tw-4]
                    write(move(mid,max(1,(tw-len(t))//2))+bg(*BG)+fg(*FG)+bold()+t+rst())
                if info.artist:
                    a=info.artist[:tw-4]
                    write(move(mid+2,max(1,(tw-len(a))//2))+bg(*BG)+fg(*DIM)+a+rst())
            elif info.title and info.status=='Stopped':
                top=3; bot=th-CAVA_BAR_ROWS-2; mid=(top+bot)//2
                for r in range(top,bot+1): write(move(r,1)+bg(*BG)+' '*tw)
                msg=f"⏹  {info.title}"
                write(move(mid,max(1,(tw-len(msg))//2))+bg(*BG)+fg(*DIM)+msg+rst())
            _draw_controls(tw,th)
            elapsed=time.monotonic()-now
            time.sleep(max(0,frame_time-elapsed))
        self.cleanup()

    def cleanup(self):
        self.cava.stop()
        if self._old_term:
            import termios
            try: termios.tcsetattr(sys.stdin.fileno(),termios.TCSADRAIN,self._old_term)
            except: pass
        write(show_cur()+clear()+move(1,1)+rst())
        print("mediactrl — bye!")

if __name__=='__main__':
    MediaController().run()
