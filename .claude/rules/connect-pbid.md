Learnings from Claude about connecting to semantic models via the connect-pbid skill

- `prlctl exec` runs outside the interactive Windows session: PBIDesktop shows `MainWindowHandle 0`, so `CloseMainWindow()`/window-title methods fail; ask the user to close/restart Desktop instead.
- zsh `echo -n` mangles `\U`/`\u` in PowerShell strings before Base64-encoding; use `printf '%s'` when building `-EncodedCommand` payloads.
- A hand-authored `.pbip` needs `$schema` matching `^https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.x.x/schema.json$` — `fabric/item/pbip/...` is rejected (UnrecognizedSchemaVersion). Pre-restart Desktop instances without the PBIP preview feature active fail *silently* on .pbip open; the toggles only apply at launch.
- Desktop rejects PBIP/PBIR files with a UTF-8 BOM; Windows PowerShell 5.1 `Set-Content -Encoding UTF8` writes a BOM — use `[System.IO.File]::WriteAllText($p, $t, (New-Object System.Text.UTF8Encoding($false)))` instead.
- VM "Windows 11": Shared Profile maps only Desktop/Documents/Downloads unless Host defined sharing is enabled; VM's C:\Users\...\Documents is local to the VM, not the Mac's ~/Documents.
