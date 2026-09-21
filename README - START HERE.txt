SEREN SPIRIT TRACKER — PORTABLE WINDOWS EDITION
================================================

No Python or Tesseract installation is required. Both are included.

FIRST RUN
---------
1. Extract the entire ZIP. Do not run the program from inside the ZIP.
2. Open the extracted "Seren Spirit Tracker" folder.
3. Double-click "Seren Spirit Tracker.exe".
4. Windows may show a SmartScreen warning because this is a personal,
   unsigned program. Choose "More info", then "Run anyway" if you trust
   the person who sent it to you.
5. The setup window will list your monitors. Choose the monitor containing
   RuneScape and adjust the capture rectangle.
6. Select "Preview Capture". The preview should show the RuneScape chatbox.
7. Select "Save Setup", then select "Start" in the main window.

RUNESCAPE SETTINGS
------------------
- Chat timestamps must be enabled.
- The chatbox must be visible while tracking.
- Chat text size 14 is preferred for reliable OCR recognition.

FILES CREATED IN THE DATA FOLDER
--------------------------------
- tracker_settings.json: monitor and capture settings
- seren_spirit_rewards.db: primary drop database
- seren_spirit_rewards.csv: compact backup/mirror
- catalyst_rewards.csv: catalyst-drop backup/mirror
- latest_ocr_debug_capture.png: most recent captured chat image
- seren_watcher_error.log: startup details if an error occurs

Use the Setup button at any time to change monitors or the capture area.
The tracker needs internet access only for Grand Exchange price refreshes.
OCR and drop storage remain local.

BACKUPS
-------
To preserve your history, back up both seren_spirit_rewards.db and
the two CSV files from the data folder. The SQLite database is the primary
copy for both Seren spirit and catalyst drops.

UPDATING
--------
The launcher silently checks for updates before opening the tracker. When a
new version is available, it offers to download, verify, and install it. The
launcher replaces only the app folder; it never replaces the data folder.

For a manual update, close the tracker, back up data as a precaution, and
replace the app folder with the one from the published update package.

When upgrading from an older release that stored data beside the executable,
the tracker automatically moves those files into the data folder.
