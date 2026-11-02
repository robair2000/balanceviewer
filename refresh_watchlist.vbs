' Hidden launcher: refresh the watchlist, then repaint the wallpaper.
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "C:\Users\robai\Documents\Standaardproject\balanceviewer"
sh.Run "cmd /c python refresh_watchlist.py >> refresh.log 2>&1 && python wallpaper.py --once --force >> refresh.log 2>&1", 0, False
