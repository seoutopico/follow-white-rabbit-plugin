# Scheduling

`/setup` registers the daily run for you. This page is the reference for manual setup or troubleshooting.

The scheduler launches `bin/cycle.ps1` (Windows) or `bin/cycle.sh` (Unix) once per day. The script's working directory must be your **project directory** (where `config.yaml` lives), not the plugin directory.

`<HH>:<MM>` below = the time you choose.
`<REPO_PATH>` = your feed project directory.
`<PLUGIN_BIN>` = `~/.claude/plugins/follow-white-rabbit/bin` (use `$env:USERPROFILE\.claude\plugins\follow-white-rabbit\bin` on Windows).
`<FEED_SLUG>` = the `combined_feed` value in your `config.yaml` (used to name the task so multiple projects don't collide).

## Windows — Task Scheduler

```powershell
$repo = "<REPO_PATH>"
$bin  = "$env:USERPROFILE\.claude\plugins\follow-white-rabbit\bin"

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$bin\cycle.ps1`"" `
    -WorkingDirectory $repo

$trigger  = New-ScheduledTaskTrigger -Daily -At <HH>:<MM>am

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

$task = New-ScheduledTask `
    -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal `
    -Description "Daily research cycle for follow-white-rabbit (<FEED_SLUG>)"

Register-ScheduledTask -TaskName "follow-white-rabbit-<FEED_SLUG>" -InputObject $task
```

Verify:

```powershell
Get-ScheduledTaskInfo -TaskName "follow-white-rabbit-<FEED_SLUG>" |
    Select-Object NextRunTime, LastRunTime, LastTaskResult
```

`StartWhenAvailable` means if the PC was off at the scheduled time, the task fires when you next log in. The 2 h `ExecutionTimeLimit` is the kill switch in case workers hang.

Disable / enable / remove:

```powershell
Disable-ScheduledTask    -TaskName "follow-white-rabbit-<FEED_SLUG>"
Enable-ScheduledTask     -TaskName "follow-white-rabbit-<FEED_SLUG>"
Unregister-ScheduledTask -TaskName "follow-white-rabbit-<FEED_SLUG>" -Confirm:$false
```

## macOS — launchd

Create `~/Library/LaunchAgents/com.follow-white-rabbit.<FEED_SLUG>.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.follow-white-rabbit.<FEED_SLUG></string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string><PLUGIN_BIN>/cycle.sh</string>
    </array>

    <key>WorkingDirectory</key>
    <string><REPO_PATH></string>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer><HH></integer>
        <key>Minute</key>
        <integer><MM></integer>
    </dict>

    <key>ExitTimeOut</key>
    <integer>1800</integer>

    <key>StandardOutPath</key>
    <string><REPO_PATH>/.logs/launchd-latest.log</string>
    <key>StandardErrorPath</key>
    <string><REPO_PATH>/.logs/launchd-latest.err</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
```

Load:

```bash
launchctl load ~/Library/LaunchAgents/com.follow-white-rabbit.<FEED_SLUG>.plist
```

Unload:

```bash
launchctl unload ~/Library/LaunchAgents/com.follow-white-rabbit.<FEED_SLUG>.plist
```

## Linux — cron

```bash
crontab -e
```

Add:

```
<MM> <HH> * * * cd <REPO_PATH> && bash <PLUGIN_BIN>/cycle.sh
```

## Linux — systemd

`~/.config/systemd/user/follow-white-rabbit-<FEED_SLUG>.service`:

```ini
[Unit]
Description=follow-white-rabbit research cycle (<FEED_SLUG>)

[Service]
Type=oneshot
WorkingDirectory=<REPO_PATH>
ExecStart=/bin/bash <PLUGIN_BIN>/cycle.sh
TimeoutStartSec=1800
```

`~/.config/systemd/user/follow-white-rabbit-<FEED_SLUG>.timer`:

```ini
[Unit]
Description=Run follow-white-rabbit (<FEED_SLUG>) daily

[Timer]
OnCalendar=*-*-* <HH>:<MM>:00
Persistent=true

[Install]
WantedBy=timers.target
```

Enable:

```bash
systemctl --user daemon-reload
systemctl --user enable --now follow-white-rabbit-<FEED_SLUG>.timer
```

## Logs

`cycle.ps1` / `cycle.sh` writes one log per day to `<REPO_PATH>/.logs/research-YYYY-MM-DD.log` and prunes anything older than 7 days. Open the latest after each run to confirm all workers finished and the publish step pushed to `gh-pages`.

## On-demand runs

| | |
|---|---|
| `/cycle` (inside Claude Code) | Full cycle, streams output |
| `/cycle --dry-run` | Shows what would be written, doesn't add entries |
| `/cycle --skip-publish` | Research runs, no push to `gh-pages` |
| `bash <PLUGIN_BIN>/cycle.sh` | Manual run from a terminal |
