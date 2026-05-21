import subprocess
import time

PWA_URL = "https://shinzef.github.io/SpenWindows/wakelock.html"

def run_adb(args):
    return subprocess.run(["adb"] + args, capture_output=True, text=True)

def setup_and_launch_pwa():
    print("Preparing tablet")
    run_adb(["shell", "settings", "put", "global", "policy_control", "immersive.full=*"])
    

    launch_cmd = [
        "shell", "am", "start",
        "-n", "com.android.chrome/com.google.android.apps.chrome.Main",
        "-a", "android.intent.action.VIEW",
        "-d", f'"{PWA_URL}"'
    ]

    subprocess.run(f"adb {' '.join(launch_cmd)}", shell=True, capture_output=True)

    run_adb(["shell", "settings", "put", "system", "screen_brightness", "0"])
    print("Done, Ctrl+C to exit.")

def restore_tablet():
    print("\nRestoring tablet")
    run_adb(["shell", "settings", "put", "global", "policy_control", "null*"])
    run_adb(["shell", "settings", "put", "global", "stay_on_while_plugged_in", "0"])
    run_adb(["shell", "settings", "put", "system", "screen_brightness", "127"])
    run_adb(["shell", "input", "keyevent", "3"]) #

if __name__ == "__main__":
    try:
        setup_and_launch_pwa()
        while True:
            time.sleep(1) 
    except KeyboardInterrupt:
        restore_tablet()