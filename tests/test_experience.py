"""Lifecycle tests use real orchestration with fake services, never reboot the host."""
from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from jarvis_app.experience import Experience
from jarvis_core.config import ToolsConfig, load
from jarvis_core.lifecycle import lifecycle_command
from tests.tools_helpers import CFG


def wait_for(pred, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return
        time.sleep(0.01)
    assert pred()


@pytest.mark.parametrize('text,expected', [
    ('Jarvis, system reboot!', 'reboot'), ('Jarvis System neustarten', 'reboot'),
    ('Bitte system neu starten.', 'reboot'), ('Hey Jarvis, wake up!', 'wake'),
    ('Wake up', 'wake'), ('Jarvis, wach auf', 'wake'), ('Jarvis geh schlafen', 'sleep'),
    ('Musik aus', 'music_stop'), ('Was bedeutet system reboot?', None),
    ('Bitte nicht neustarten', None), ('Sag mir, was wake up heißt', None),
    ('Fahre meinen Mac herunter', None), ('Öffne die Datei reboot.txt', None),
])
def test_exact_commands(text, expected):
    assert lifecycle_command(text) == expected


class Hub:
    connected = True
    def __init__(self):
        self.spoken, self.standby = [], False
        self.completions = []
    def say(self, line, completion_id=None):
        self.spoken.append(line)
        if completion_id:
            self.completions.append(completion_id)
        return True
    def set_standby(self, value): self.standby = value
    def stop_speaking(self): pass


class Host:
    def __init__(self, tmp_path):
        self._cfg = replace(CFG, tools=ToolsConfig(data_dir=str(tmp_path)),
                            experience=replace(CFG.experience, music_file=None))
        self._hub = Hub()
        self._startup_mode = 'boot'
        self._window = object()
        self.events, self.collected, self.closed = [], 0, 0
        self.restart_requested = False
    def _js(self, name, *args): self.events.append((name, json.loads(json.dumps(args))))
    def show_panel(self): self.events.append(('show_panel', []))
    def hide_panel(self): self.events.append(('hide_panel', []))
    def cancel(self, **kwargs): pass
    def quit_app(self): self.closed += 1
    def _ensure_briefing(self): return self
    def collect(self):
        self.collected += 1
        return {'datetime': 'Mittwoch, 28. Oktober, 10 Uhr 00',
                'weather': {'ort': 'Hamburg', 'temp_now': 13, 'temp_max': 16, 'temp_min': 8, 'text': 'klar'},
                'mails': [], 'issues': [], 'system': {'cpu': 1, 'ram': 2}, 'repo': 'x/y'}


@pytest.fixture
def xp(tmp_path):
    host = Host(tmp_path)
    exp = Experience(host)
    exp.STEP_PAUSE = 0
    exp.SHUTDOWN_SECONDS = .05
    exp.VOICE_WAIT_S = 0
    exp.opened = []
    exp.open_url = exp.opened.append
    exp._check_memory = lambda: ('ok', 'Datenbank erreichbar')
    exp._check_model = lambda: ('ok', 'Testmodell geladen')
    return exp


def test_boot_collects_real_service_result_and_reports_missing_music(xp):
    assert xp.start('boot')['ok']
    wait_for(lambda: xp.state['phase'] == 'ready')
    assert xp.host.collected == 1
    assert xp.state['progress'] == 100
    assert next(s for s in xp.state['steps'] if s['id'] == 'music')['state'] == 'skip'
    assert any('13 Grad' in line for line in xp.host._hub.spoken)
    assert not xp.host.restart_requested


def test_model_failure_is_not_shown_as_ready(xp):
    xp._check_model = lambda: ('warn', 'Ollama nicht erreichbar')
    xp.start('boot')
    wait_for(lambda: xp.state['phase'] == 'ready')
    assert xp.state['warnings'] == 1
    assert next(s for s in xp.state['steps'] if s['id'] == 'model')['state'] == 'warn'


def test_visible_startup_keeps_quick_model_warmup(xp, monkeypatch):
    loaded = []
    xp.host._cfg = replace(xp.host._cfg, profile=replace(xp.host._cfg.profile, quick='quick-model'))
    monkeypatch.setattr('jarvis_app.experience.OllamaClient', lambda *a, **k: SimpleNamespace(
        ping=lambda: True, preload=lambda model, keep, num_ctx=None: (loaded.append((model, num_ctx)) or True)))
    status, _ = Experience._check_model(xp)
    assert status == 'ok'
    ctx = xp.host._cfg.profile.num_ctx           # gleicher Kontext wie der Chat ⇒ kein Neuladen
    assert loaded == [(xp.host._cfg.profile.general, ctx), ('quick-model', ctx)]


def test_one_sequence_at_a_time_and_no_stale_briefing_after_sleep(xp):
    entered, release = threading.Event(), threading.Event()
    def slow():
        entered.set(); release.wait(3)
        return Host.collect(xp.host)
    xp.host.collect = slow
    xp.start('boot')
    assert entered.wait(1)
    assert not xp.start('boot')['ok']
    xp.start('sleep')
    release.set()
    time.sleep(.2)
    assert xp.sleeping and xp.host._hub.standby
    assert not xp.host._hub.spoken
    assert not any(name == 'jarvis.onBriefing' for name, _ in xp.host.events)


def test_briefing_reaches_late_voice_connection_once(xp):
    xp.host._hub.connected = False
    xp.start('boot')
    wait_for(lambda: xp.state['phase'] == 'ready')
    assert not xp.host._hub.spoken
    xp.host._hub.connected = True
    xp.voice_connected()
    count = len(xp.host._hub.spoken)
    xp.voice_connected()
    assert count > 1 and len(xp.host._hub.spoken) == count


def test_startup_waits_for_daemon_started_alongside_the_app(xp):
    xp.VOICE_WAIT_S = 5
    xp.host._hub.connected = False
    xp.start('boot')
    wait_for(lambda: any(s['id'] == 'voice' and s['state'] == 'loading' for s in xp.state['steps']))
    assert xp.state['phase'] == 'running'
    xp.host._hub.connected = True
    wait_for(lambda: xp.state['phase'] == 'ready')
    assert next(s for s in xp.state['steps'] if s['id'] == 'voice')['state'] == 'ok'


def test_cancel_ends_the_daemon_wait_immediately(xp):
    xp.VOICE_WAIT_S = 30
    xp.host._hub.connected = False
    xp.start('boot')
    wait_for(lambda: any(s['id'] == 'voice' and s['state'] == 'loading' for s in xp.state['steps']))
    started = time.monotonic()
    xp.cancel()
    assert xp.state['phase'] == 'cancelled' and time.monotonic() - started < 1.5


def test_cancel_discards_pending_speech(xp):
    xp.host._hub.connected = False
    xp.start('boot')
    wait_for(lambda: xp.state['phase'] == 'ready')
    xp.cancel()
    xp.host._hub.connected = True
    xp.voice_connected()
    assert not xp.host._hub.spoken


class FakeMusic:
    def __init__(self):
        self.playing = self.ducked = False
        self.finished = 0
    def start(self, path):
        self.playing = True
        return True, path.name
    def stop(self):
        self.finished += int(self.playing)
        self.playing = False
    def duck(self, value): self.ducked = value
    def snapshot(self): return {'playing': self.playing, 'title': 'Back In Black', 'error': ''}


@pytest.fixture
def music_xp(xp, tmp_path):
    xp.host._cfg = replace(xp.host._cfg, experience=replace(
        xp.host._cfg.experience, music_file=str(tmp_path / 'Back In Black.mp3')))
    xp.music = FakeMusic()
    return xp


def test_music_waits_for_final_briefing_playback_not_idle_or_checks(music_xp):
    xp = music_xp
    xp.start('boot')
    wait_for(lambda: bool(xp.host._hub.completions))
    assert xp.state['phase'] == 'ready' and xp.state['progress'] == 100
    assert xp.music.playing and xp.music.ducked
    xp.voice_state('idle')  # A pause between spoken sentences is not the end of the report.
    xp.speech_finished('unrelated-answer')
    assert xp.music.playing and xp.music.ducked
    xp.speech_finished(xp.host._hub.completions[-1])
    assert not xp.music.playing and xp.music.finished == 1


def test_music_ends_when_report_is_displayed_without_voice(music_xp):
    xp = music_xp
    xp.host._hub.connected = False
    xp.start('boot')
    wait_for(lambda: xp.state['phase'] == 'ready' and not xp.music.playing)
    assert xp.music.finished == 1
    assert any(name == 'jarvis.onBriefing' for name, _ in xp.host.events)
    xp.host._hub.connected = True
    xp.voice_connected()
    assert xp.host._hub.spoken and not xp.music.playing


def test_disconnect_stops_music_and_old_completion_cannot_stop_next_boot(music_xp):
    xp = music_xp
    xp.start('boot')
    wait_for(lambda: len(xp.host._hub.completions) == 1)
    previous = xp.host._hub.completions[-1]
    xp.voice_disconnected()
    assert not xp.music.playing
    xp.start('boot')
    wait_for(lambda: len(xp.host._hub.completions) == 2)
    xp.speech_finished(previous)
    assert xp.music.playing
    xp.speech_finished(xp.host._hub.completions[-1])
    assert not xp.music.playing


def test_reboot_requests_app_exit_once_and_can_be_cancelled(xp):
    xp.start('reboot')
    assert not xp.start('reboot')['ok']
    wait_for(lambda: xp.host.closed == 1)
    assert xp.host.restart_requested


def test_cancel_reboot_keeps_app_open(xp):
    xp.SHUTDOWN_SECONDS = .2
    xp.start('reboot')
    xp.cancel()
    time.sleep(.25)
    assert not xp.host.restart_requested and xp.host.closed == 0


def test_restarted_process_initializes_instead_of_restarting_again(xp):
    xp.host._startup_mode = 'reboot'
    xp.ready()
    xp.ready()
    wait_for(lambda: xp.state['phase'] == 'ready')
    assert xp.host.closed == 0 and xp.host.collected == 1
    assert any('Wieder für Sie da' in line for line in xp.host._hub.spoken)


def test_relaunch_preserves_configuration_and_only_targets_jarvis(tmp_path):
    import sys
    from jarvis_app.main import restart_argv
    args = SimpleNamespace(config=tmp_path / 'my config.yaml', profile='mac', debug=True)
    argv = restart_argv(args)
    assert argv[:3] == [sys.executable, '-m', 'jarvis_app.main']
    assert argv[argv.index('--config') + 1] == str(args.config)
    assert argv.count('--reboot') == 1 and '--debug' in argv
    assert not any(s in argv for s in ('shutdown', 'sudo', 'osascript'))


def test_config_music_is_optional_and_volume_is_bounded(tmp_path):
    p = tmp_path / 'c.yaml'
    p.write_text('experience:\n  music_volume: 7\n  music_duck_volume: -1\n  music_file: "track.mp3"\n')
    c = load(p)
    assert c.experience.music_volume == 1 and c.experience.music_duck_volume == 0
    assert c.experience.music_file == 'track.mp3'


def test_music_streams_without_using_real_speakers(tmp_path, monkeypatch):
    import sys
    import numpy as np
    import soundfile as sf
    from jarvis_core.background_music import BackgroundMusic
    path = tmp_path / 'audio sample.wav'
    sf.write(path, np.ones((16000, 2), dtype='float32') * .5, 16000)
    blocks = []
    class Output:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def write(self, samples): blocks.append(samples.copy())
    monkeypatch.setitem(sys.modules, 'sounddevice', SimpleNamespace(OutputStream=Output))
    music = BackgroundMusic(volume=.3, duck_volume=.06)
    music.duck(True)
    ok, _ = music.start(path)
    assert ok
    wait_for(lambda: not music.playing)
    assert len(blocks) > 1 and max(len(b) for b in blocks) <= 640
    assert max(float(abs(b).max()) for b in blocks) <= .031
    assert music.start(tmp_path / 'missing.mp3')[0] is False


def test_standby_stops_queued_speech_but_keeps_wake_microphone():
    from voice_daemon.main import Daemon
    d = Daemon.__new__(Daemon)
    d._speech_generation = 0
    d._speech_lock = threading.Lock()
    d._speak_q = queue.Queue()
    d._pending_speak = True
    d._drop_speak = False
    stopped, states = [], []
    d.mouth = SimpleNamespace(stop=lambda: stopped.append(True))
    d.link = SimpleNamespace(send_state=states.append)
    d.ears = SimpleNamespace(_listen_now=threading.Event(), muted=False)
    d._speak_q.put(('Alter Wetterbericht', True, 0, None))
    d._on_standby(True)
    d._on_speak('Nicht vorlesen')
    remaining = list(d._speak_q.queue)
    assert remaining == [('', False, 1, None)]
    assert stopped and not d.ears.muted and d._standby
    d._on_standby(False)
    assert not d._standby


def test_bridge_routes_lifecycle_before_any_model_or_tool(tmp_path):
    from tests.test_bridge import make_bridge
    bridge = make_bridge(tmp_path)
    calls = []
    bridge.request_experience = lambda mode: (calls.append(mode) or {'ok': True, 'message': 'Start'})
    bridge._runtime = lambda: pytest.fail('Lifecycle must not execute a tool or command')
    for text in ('Jarvis system reboot', 'Jarvis wake up', 'Jarvis geh schlafen'):
        bridge._run_stream('s1', text, False)
    assert calls == ['reboot', 'wake', 'sleep']


def test_bridge_standby_blocks_other_actions(tmp_path):
    from tests.test_bridge import make_bridge
    bridge = make_bridge(tmp_path)
    bridge._experience.state = {'mode': 'sleep', 'phase': 'standby', 'steps': [], 'progress': 0}
    bridge._runtime = lambda: pytest.fail('Standby must not execute an action')
    bridge._run_stream('s1', 'Starte Safari', False)
    assert any('Standby' in call for call in bridge._window.js_calls)


def test_cancel_suppresses_a_late_chat_response(tmp_path):
    from tests.test_bridge import make_bridge
    bridge = make_bridge(tmp_path)
    bridge._cfg = replace(bridge._cfg, tools=ToolsConfig(data_dir=str(tmp_path)))
    def send(*args, **kwargs):
        yield {'type': 'token', 'content': 'Vor dem Abbruch. '}
        bridge.cancel()
        yield {'type': 'token', 'content': 'DARF NICHT VORGELESEN WERDEN. '}
    bridge._chat.send = send
    bridge._run_stream('s1', 'Erzähle eine Geschichte', False, True, True)
    assert bridge._hub.spoken == ['Vor dem Abbruch.']


# ---------------------------------------------------------------- Aufwachen ≠ Systemstart

def test_wake_from_standby_is_quick_without_checks_briefing_or_music(xp):
    xp.WAKE_SECONDS = .05
    xp.start('sleep')
    wait_for(lambda: xp.state['phase'] == 'standby')
    assert ('hide_panel', []) in xp.host.events            # Chat weg, nur der Orb bleibt
    assert xp.start('wake')['ok']
    wait_for(lambda: xp.state['phase'] == 'awake')
    assert xp.state['steps'] == [] and xp.host.collected == 0 and not xp.host._hub.spoken
    wait_for(lambda: ('show_panel', []) in xp.host.events)


def test_hey_jarvis_while_awake_is_not_a_wake_sequence(xp):
    r = xp.start('wake')
    assert r.get('noop') and r['message'] == 'Ja, Sir?'
    assert xp.state['phase'] == 'idle' and xp.host.collected == 0


def test_music_fallback_stops_song_if_completion_never_arrives(music_xp):
    xp = music_xp
    xp.MUSIC_FALLBACK_S = 0.2
    xp.host._hub.say = lambda line, completion_id=None: True    # alter Daemon: meldet nie das Ende
    xp.start('boot')
    wait_for(lambda: xp.state['phase'] == 'ready')
    assert xp.music.playing
    wait_for(lambda: not xp.music.playing, timeout=60)
    assert xp.music.finished == 1


def test_briefing_completion_id_is_on_the_last_line_only(xp):
    seen = []
    xp.host._hub.say = lambda line, completion_id=None: (seen.append((line, completion_id)) or True)
    xp.start('boot')
    wait_for(lambda: xp.state['phase'] == 'ready' and seen)
    assert all(cid is None for _, cid in seen[:-1]) and seen[-1][1]


@pytest.mark.parametrize("text,expected", [
    ("Jarvis geht schlafen", "sleep"), ("Jarvis, geh mal schlafen", "sleep"), ("Geh in den Ruhemodus", "sleep"),
    ("Schlaf gut, Jarvis", "sleep"), ("Wach mal auf", "wake"), ("Jarvis aufwachen", "wake"),
    ("Wie lange sollte ich schlafen?", None), ("Ich gehe gleich schlafen und du?", None),
])
def test_lifecycle_tolerates_speech_recognition_variants(text, expected):
    assert lifecycle_command(text) == expected


def test_startup_screen_stays_until_briefing_is_spoken(xp):
    xp.start('boot')
    wait_for(lambda: bool(xp.host._hub.completions))
    assert xp.state['phase'] == 'ready' and xp.state['speaking']
    xp.speech_finished(xp.host._hub.completions[-1])
    assert not xp.state['speaking']
    published = [args[0] for name, args in xp.host.events if name == 'jarvis.onExperience']
    assert published[-1]['speaking'] is False


def test_briefing_data_reaches_the_ui_before_ready(xp):
    xp.start('boot')
    wait_for(lambda: xp.state['phase'] == 'ready')
    names = [name for name, args in xp.host.events
             if name == 'jarvis.onBriefing' or (name == 'jarvis.onExperience' and args[0]['phase'] == 'ready')]
    assert names[0] == 'jarvis.onBriefing'


def test_start_windows_open_on_boot_but_not_after_reboot(xp):
    urls = xp.host._cfg.experience.open_on_start
    assert urls, 'config.yaml sollte Startlinks enthalten'
    xp.start('boot')
    wait_for(lambda: len(xp.opened) == len(urls))
    assert xp.opened == list(urls)
    xp.opened.clear()
    xp.host._startup_mode = 'reboot'
    xp.start('boot')
    wait_for(lambda: xp.state['phase'] == 'ready')
    time.sleep(0.1)
    assert xp.opened == []


def test_config_only_accepts_web_links_to_open(tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text(open('config.yaml', encoding='utf-8').read().replace(
        '    - "https://calendar.google.com/calendar/r/day"',
        '    - "https://calendar.google.com/calendar/r/day"\n    - "file:///etc/passwd"'), encoding='utf-8')
    assert all(u.startswith('https://') for u in load(str(path)).experience.open_on_start)
