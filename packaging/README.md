# Deferral Twin

A digital twin of you that runs entirely on this Mac. It answers the way you would, and says “I don’t know” when nothing you told it is close enough.

**You need:** a Mac with Apple Silicon (M1 or newer), 16 GB of memory, macOS 14 Sonoma or newer, about 12 GB of free disk, and an internet connection for the first setup only.

## Install

1. Drag **Deferral Twin** onto the **Applications** folder in this window.
2. Open **Applications** and double-click **Deferral Twin**.

### If macOS says it “cannot verify” the app

The app isn’t notarised by Apple (it’s a course project, signed on the Mac that built it), so a downloaded copy is blocked the first time. This is a one-off:

1. In the warning, click **Done**. Do not click “Move to Bin”.
2. Open **System Settings**, then **Privacy & Security**.
3. Scroll down to **Security**. You’ll see *“Deferral Twin” was blocked to protect your Mac.* Click **Open Anyway**.
4. Enter your Mac password or use Touch ID, then click **Open Anyway** again.

From then on the app opens normally.

Control-click → Open no longer gets past this on current macOS, so use the steps above.

![Privacy & Security, with the Open Anyway button next to the message about Deferral Twin](gatekeeper.png)

## First launch

The app sets itself up. You don’t need a terminal, Homebrew or Python.

1. **Welcome.** It shows how much it will download, measured from the servers at that moment.
2. **Machine check.** Apple Silicon, memory, macOS and free disk are checked before anything is downloaded. If something is wrong, it tells you what and stops.
3. **Download and prepare.** Seven models and three environments, each with its own progress bar. If you quit or lose the connection, the next launch carries on from where it stopped.
4. **Self-test.** One answer, one spoken sentence and a short lip-sync clip.
5. **Consent**, then the interview.

After setup the app works with Wi-Fi off.

macOS asks once for the **microphone** (to record your voice) and once for the **camera** (for your photo). You can refuse both and choose files instead.

## Where your data is

Everything lives in one folder: `~/Library/Application Support/DeferralTwin/`. Your answers, voice, photo and anything you teach the twin never leave this Mac.

- **Settings → Export my data** saves a zip of everything you gave it to Downloads.
- **Settings → Delete my twin** erases your answers, voice, photo, corrections and clips, and returns to the consent screen. The downloaded models stay, so a new twin needs no new download.

## Uninstall

**Trashing the app alone leaves roughly 10 GB behind** in `~/Library/Application Support/DeferralTwin/`.

To remove everything:

1. Open the app, go to **Settings → Remove everything**, and type your name to confirm. This deletes the whole folder.
2. Drag **Deferral Twin** from Applications to the Bin.

If the app no longer opens, delete the folder by hand: in Finder choose **Go → Go to Folder…**, paste `~/Library/Application Support/`, and move **DeferralTwin** to the Bin.

## If something goes wrong

- Setup stopped: open the app again. It resumes, and shows **Retry** on the step that failed.
- The setup log is in `~/Library/Application Support/DeferralTwin/logs/setup.log`, the app’s in `logs/server.log`, and the launcher’s in `~/Library/Logs/DeferralTwin.log`.
