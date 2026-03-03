# 🚀 LDPlayer Automation Tool

A powerful multi-instance automation tool for **LDPlayer** built with **Python + PySide6 + ADB**.

Control, launch, automate, and manage multiple LDPlayer instances from one desktop application.

---

## ✨ Features

### 🖥 Instance Management
- Scan all LDPlayer instances
- Start / Stop selected instances
- Auto-detect ADB device serial
- Multi-instance support

### 📱 App Launcher
- Launch apps (e.g. Facebook, Surfshark) on selected instances
- Force stop app
- Per-instance status logging

### 🤖 Macro Engine
- JSON-based macro system
- Supports:
  - `wait`
  - `tap`
  - `swipe`
  - `text`
  - `keyevent`
- Run macro on:
  - Single instance
  - Multiple instances (parallel)
  - Multiple instances (staggered)
- Works even when LDPlayer is minimized (ADB-based control)

### 🪟 Window Management
- Arrange selected LD windows in grid layout
- Multi-monitor support
- Restore / Minimize / Bring to front
- Designed for managing 5–20 instances easily

---

## 🏗 Project Structure


ldtool/
│
├── app.py
├── config.example.json
├── macros/
│
├── src/
│ ├── core/
│ │ ├── adb_manager.py
│ │ ├── ldplayer_controller.py
│ │ ├── macro_engine.py
│ │ ├── models.py
│ │ └── window_manager.py
│ │
│ └── ui/
│ ├── main_window.py
│ ├── instances_page.py
│ ├── app_launcher_page.py
│ └── macro_runner_page.py
│
└── assets/


---

## ⚙ Requirements

- Windows 10/11
- Python 3.10+
- LDPlayer 9
- ADB enabled in LDPlayer settings

---

## 🔧 Installation

### 1️⃣ Clone repository

```bash
git clone https://github.com/your-username/ldplayer-automation-tool.git
cd ldplayer-automation-tool
2️⃣ Create virtual environment
python -m venv .venv
.venv\Scripts\activate
3️⃣ Install dependencies
pip install -r requirements.txt

Or manually:

pip install pyside6 adbutils psutil pywin32 pyinstaller
🛠 Setup Configuration

Copy:

config.example.json

Rename to:

config.json

Edit paths:

{
  "ldplayer_dir": "C:/Path/To/LDPlayer",
  "dnconsole_path": "C:/Path/To/LDPlayer/dnconsole.exe",
  "adb_path": "C:/Path/To/LDPlayer/adb.exe"
}
▶ Running The App
python app.py
📦 Build EXE (Optional)
pyinstaller --noconsole --onefile app.py

Executable will be in:

dist/
📝 Macro Format

Example macro:

{
  "name": "fb_scroll_like",
  "steps": [
    { "wait": 1200 },
    { "swipe": [540, 1600, 540, 500, 400] },
    { "wait": 900 },
    { "tap": [930, 1260] },
    { "wait": 1500 }
  ]
}
Supported Actions
Action	Format
wait	{ "wait": 1200 }
tap	{ "tap": [x, y] }
swipe	{ "swipe": [x1, y1, x2, y2, duration] }
text	{ "text": "hello" }
keyevent	{ "keyevent": "HOME" }
⚡ Architecture Highlights

ADB-based control (no screen automation)

Works minimized / background

Shared AppState across tabs

Multi-threaded execution (UI never freezes)

Window arrangement via Win32 API

🛡 Notes

Run as Administrator if dnconsole requires elevation.

Disable LDPlayer “pause when minimized” setting for best performance.

Make sure ADB debugging is enabled in LDPlayer.

🧠 Future Improvements

Coordinate auto-scaling per resolution

Random jitter (anti-detection)

Conditional macros

Image-based detection (OpenCV)

Macro recorder inside tool

Scheduled automation

📄 License

MIT License

👨‍💻 Author

Built with ❤️ using Python + PySide6 + ADB


---

# 🔥 Optional Upgrade

If you want, I can also:

- Make it **GitHub-ready with badges**
- Add architecture diagram
- Add screenshots section
- Add version 1.0 release notes
- Help you prepare for public release

You’ve built something serious now 💪