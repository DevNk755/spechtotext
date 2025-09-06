kivy>=2.3.0
SpeechRecognition>=3.10.0
pyttsx3>=2.90
pyaudio>=0.2.14; platform_system == "Linux" or platform_system == "Darwin"
# On Windows, install PyAudio via pipwin if regular install fails:
# pip install pipwin && pipwin install pyaudio

# Optional alternatives
# sounddevice>=0.4.6  # If you plan to use an alternative to PyAudio (requires code changes)
