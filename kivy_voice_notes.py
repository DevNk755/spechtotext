"""
Kivy Voice Notes (Single-file)

Features:
- Text input area for typing or appending recognized speech
- Buttons: Record, Stop, Speak, Save, Load, New
- Status label showing current action
- Non-blocking: ASR and TTS run in background threads
- ASR: SpeechRecognition with Google recognizer (online) by default
- TTS: pyttsx3 (offline)
- Cross-platform; PyAudio note for Windows

Run:
    python kivy_voice_notes.py

Dependencies (pip):
    kivy
    SpeechRecognition
    pyttsx3
    pyaudio (Linux/macOS) or pipwin+pyaudio (Windows)

On Windows, if PyAudio fails:
    pip install pipwin
    pipwin install pyaudio
"""

import threading
import queue
import time
from datetime import datetime
from pathlib import Path

from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.uix.filechooser import FileChooserIconView
from kivy.uix.popup import Popup
from kivy.clock import Clock

# ASR
try:
    import speech_recognition as sr
except Exception:
    sr = None

# TTS
try:
    import pyttsx3
except Exception:
    pyttsx3 = None


class VoiceNotesKivy(App):
    def build(self):
        self.title = "Kivy Voice Notes"
        self.base_dir = Path(__file__).parent
        self.notes_dir = self.base_dir / "notes"
        self.notes_dir.mkdir(exist_ok=True)

        # State: ASR
        self.recognizer = sr.Recognizer() if sr else None
        self.is_listening = False
        self.listen_stop_event = threading.Event()
        self.results_queue: "queue.Queue[str]" = queue.Queue()

        # State: TTS (queue multiple speak tasks)
        self.tts_engine = pyttsx3.init() if pyttsx3 else None
        self.is_speaking = False
        self.tts_thread: threading.Thread | None = None
        self.tts_queue: "queue.Queue[str]" = queue.Queue()
        self.tts_stop_event = threading.Event()

        # UI
        root = BoxLayout(orientation="vertical", padding=8, spacing=8)

        # Buttons row
        btn_row = GridLayout(cols=6, size_hint_y=None, height=44, spacing=6)
        self.btn_record = Button(text="Record")
        self.btn_stop = Button(text="Stop")
        self.btn_speak = Button(text="Speak")
        self.btn_save = Button(text="Save")
        self.btn_load = Button(text="Load")
        self.btn_new = Button(text="New")
        self.btn_record.bind(on_release=lambda *_: self.on_record())
        self.btn_stop.bind(on_release=lambda *_: self.on_stop_button())
        self.btn_speak.bind(on_release=lambda *_: self.on_speak())
        self.btn_save.bind(on_release=lambda *_: self.on_save())
        self.btn_load.bind(on_release=lambda *_: self.on_load())
        self.btn_new.bind(on_release=lambda *_: self.on_new())
        for b in (self.btn_record, self.btn_stop, self.btn_speak, self.btn_save, self.btn_load, self.btn_new):
            btn_row.add_widget(b)

        # Text area
        self.text_area = TextInput(text="", multiline=True, font_size=16)

        # Status label
        self.status_label = Label(text="Ready", size_hint_y=None, height=28, halign="left", valign="middle")
        self.status_label.bind(size=lambda *_: setattr(self.status_label, 'text_size', self.status_label.size))

        root.add_widget(btn_row)
        root.add_widget(self.text_area)
        root.add_widget(self.status_label)

        # Poll ASR queue
        Clock.schedule_interval(self._process_results_queue, 0.1)

        self._update_buttons()
        return root

    # ---------- Button handlers ----------
    def on_record(self):
        if self.is_listening:
            return
        if not self.recognizer or not sr:
            self._error_popup("Missing Dependency", "speech_recognition is not available. Please install it.")
            return
        self.is_listening = True
        self.listen_stop_event.clear()
        self._set_status("Listening...")
        self._update_buttons()
        threading.Thread(target=self._listen_loop, daemon=True).start()

    def on_stop_button(self):
        # Stop listening if active
        if self.is_listening:
            self.listen_stop_event.set()
            self._set_status("Stopping listening...")
        # Stop speaking/queued TTS if active
        speaking_or_queued = self.is_speaking or (not self.tts_queue.empty())
        if speaking_or_queued and self.tts_engine:
            self.tts_stop_event.set()
            self._clear_tts_queue()
            try:
                self.tts_engine.stop()
            except Exception:
                pass
            # Reset engine to avoid one-time speak issues on some systems
            self.tts_engine = None
            self.tts_thread = None
            self.is_speaking = False
            self._set_status("Stopped.")
        if not self.is_listening and not speaking_or_queued:
            self._set_status("Nothing to stop.")
        self._update_buttons()

    def on_speak(self):
        if not pyttsx3:
            self._error_popup("Missing Dependency", "pyttsx3 is not available. Please install it.")
            return
        # Re-init engine if it was reset/stopped previously
        if self.tts_engine is None:
            try:
                self.tts_engine = pyttsx3.init()
            except Exception as e:
                self._error_popup("TTS Error", f"Engine init failed: {e}")
                return
        text = self.text_area.text.strip()
        if not text:
            self._set_status("Nothing to speak.")
            return
        # Enqueue text and ensure worker is running
        self.tts_queue.put(text)
        self._set_status("Queued for speaking...")
        if not self.tts_thread or not self.tts_thread.is_alive():
            self.tts_stop_event.clear()
            self.tts_thread = threading.Thread(target=self._tts_worker, daemon=True)
            self.tts_thread.start()
        self._update_buttons()

    def on_save(self):
        try:
            self.notes_dir.mkdir(exist_ok=True)
            default_name = f"note_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            # Simple save: choose name with a popup TextInput
            self._save_dialog(default_name)
        except Exception as e:
            self._error_popup("Save Error", str(e))
            self._set_status("Save failed.")

    def on_load(self):
        try:
            self._open_file_dialog()
        except Exception as e:
            self._error_popup("Load Error", str(e))
            self._set_status("Load failed.")

    def on_new(self):
        self.text_area.text = ""
        self._set_status("Cleared.")

    # Called by Kivy when the app is closing
    def on_stop(self):  # type: ignore[override]
        # Stop listening
        if self.is_listening:
            self.listen_stop_event.set()
        # Stop TTS worker
        if self.tts_engine:
            self.tts_stop_event.set()
            try:
                self.tts_engine.stop()
            except Exception:
                pass

    # ---------- Internals ----------
    def _update_buttons(self):
        speaking_or_queued = self.is_speaking or (not self.tts_queue.empty())
        # While listening: disable record/speak; enable stop
        self.btn_record.disabled = self.is_listening
        self.btn_speak.disabled = self.is_listening
        # Stop enabled if listening or speaking/queued
        self.btn_stop.disabled = not (self.is_listening or speaking_or_queued)
        # Save/Load/New always enabled
        self.btn_save.disabled = False
        self.btn_load.disabled = False
        self.btn_new.disabled = False

    def _set_status(self, text: str):
        def _update(dt):
            self.status_label.text = text
        Clock.schedule_once(_update, 0)

    def _process_results_queue(self, dt):
        try:
            while True:
                chunk = self.results_queue.get_nowait()
                if chunk:
                    if not chunk.endswith(" "):
                        chunk += " "
                    self.text_area.text += chunk
        except queue.Empty:
            pass

    # ---------- ASR thread ----------
    def _listen_loop(self):
        assert self.recognizer is not None
        try:
            with sr.Microphone() as source:
                self.recognizer.adjust_for_ambient_noise(source, duration=0.5)
                while not self.listen_stop_event.is_set():
                    try:
                        audio = self.recognizer.listen(source, timeout=2, phrase_time_limit=6)
                    except sr.WaitTimeoutError:
                        continue
                    try:
                        text = self.recognizer.recognize_google(audio)
                        self.results_queue.put(text)
                        self._set_status("Recognized.")
                    except sr.UnknownValueError:
                        self._set_status("Could not understand audio.")
                    except sr.RequestError as e:
                        self._set_status(f"ASR service error: {e}")
                        time.sleep(0.5)
        except OSError as e:
            self._error_popup("Microphone Error", str(e))
            self._set_status("Microphone error.")
        except Exception as e:
            self._error_popup("Listening Error", str(e))
            self._set_status("Listening error.")
        finally:
            self.is_listening = False
            self.listen_stop_event.clear()
            self._set_status("Ready")
            Clock.schedule_once(lambda *_: self._update_buttons(), 0)

    # ---------- TTS worker (queued) ----------
    def _tts_worker(self):
        while not self.tts_stop_event.is_set():
            try:
                text = self.tts_queue.get(timeout=0.2)
            except queue.Empty:
                # If nothing to say, update state and keep waiting
                if self.is_speaking:
                    self.is_speaking = False
                    self._set_status("Ready")
                    Clock.schedule_once(lambda *_: self._update_buttons(), 0)
                continue

            if not text:
                continue

            # Ensure engine exists (can be reset after stop/errors)
            if self.tts_engine is None and pyttsx3:
                try:
                    self.tts_engine = pyttsx3.init()
                except Exception as e:
                    self._error_popup("TTS Error", f"Engine init failed: {e}")
                    continue

            if not self.tts_engine:
                continue

            # Start speaking this queued item
            self.is_speaking = True
            self._set_status("Speaking...")
            Clock.schedule_once(lambda *_: self._update_buttons(), 0)
            try:
                self.tts_engine.say(text)
                self.tts_engine.runAndWait()  # blocking until finished
            except Exception as e:
                self._error_popup("TTS Error", str(e))
                self._set_status("TTS error.")
                # Force re-init next time
                self.tts_engine = None
            finally:
                self.tts_queue.task_done()

        # Stopped externally
        self.is_speaking = False
        self._set_status("Ready")
        Clock.schedule_once(lambda *_: self._update_buttons(), 0)

    def _clear_tts_queue(self):
        try:
            while True:
                self.tts_queue.get_nowait()
                self.tts_queue.task_done()
        except queue.Empty:
            pass

    # ---------- Dialogs ----------
    def _open_file_dialog(self):
        chooser = FileChooserIconView(path=str(self.notes_dir), filters=["*.txt"]) 
        popup = Popup(title="Load Note", content=chooser, size_hint=(0.9, 0.9))

        def load_selected(instance):
            if chooser.selection:
                try:
                    path = Path(chooser.selection[0])
                    content = path.read_text(encoding="utf-8")
                    self.text_area.text = content
                    self._set_status(f"Loaded: {path}")
                except Exception as e:
                    self._error_popup("Load Error", str(e))
            popup.dismiss()

        chooser.bind(on_submit=lambda inst, sel, touch: load_selected(inst))
        popup.open()

    def _save_dialog(self, default_name: str):
        layout = BoxLayout(orientation="vertical", spacing=8, padding=8)
        name_input = TextInput(text=default_name, multiline=False)
        buttons = BoxLayout(size_hint_y=None, height=44, spacing=8)
        btn_ok = Button(text="Save")
        btn_cancel = Button(text="Cancel")
        buttons.add_widget(btn_ok)
        buttons.add_widget(btn_cancel)
        layout.add_widget(Label(text="Filename:"))
        layout.add_widget(name_input)
        layout.add_widget(buttons)
        popup = Popup(title="Save Note", content=layout, size_hint=(0.8, 0.4))

        def do_save(*_):
            try:
                name = name_input.text.strip() or default_name
                if not name.endswith(".txt"):
                    name += ".txt"
                path = self.notes_dir / name
                path.write_text(self.text_area.text, encoding="utf-8")
                self._set_status(f"Saved to {path}")
            except Exception as e:
                self._error_popup("Save Error", str(e))
            popup.dismiss()

        btn_ok.bind(on_release=do_save)
        btn_cancel.bind(on_release=lambda *_: popup.dismiss())
        popup.open()

    def _error_popup(self, title: str, msg: str):
        content = BoxLayout(orientation="vertical", padding=8, spacing=8)
        content.add_widget(Label(text=msg))
        btn_close = Button(text="Close", size_hint_y=None, height=44)
        content.add_widget(btn_close)
        popup = Popup(title=title, content=content, size_hint=(0.8, 0.4))
        btn_close.bind(on_release=lambda *_: popup.dismiss())
        popup.open()


if __name__ == "__main__":
    VoiceNotesKivy().run()