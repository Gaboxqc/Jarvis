; Installer hooks — REQ-29.
;
; NSIS already closes kai.exe before writing over it, because that is the
; application it was told about. It knows nothing about kai-backend.exe, which
; is a resource rather than the app, and which holds its own copies of the
; Visual C++ runtime open for as long as it runs. Overwriting those is the
; first thing the installer tries, so a backend left running turns an upgrade
; into:
;
;     Error opening file for writing:
;     ...\resources\kai-backend\_internal\MSVCP140_1.dll
;
; with Abort / Retry / Ignore, where Ignore quietly produces a half-written
; install that fails at launch.
;
; The backend now exits on its own when the app does, so this should never have
; anything to close. It stays because "should never" is what was believed the
; first time: the app's own shutdown path was already correct and still left an
; orphan, because it never ran. This closes the process the installer is about
; to overwrite, which is true regardless of how it got there.

!macro NSIS_HOOK_PREINSTALL
  DetailPrint "Closing any running Kai backend..."
  ; /T ends the process tree, in case a skill left a child of its own behind.
  ; Failure is not an error: not running is the expected case.
  nsExec::Exec '"$SYSDIR\taskkill.exe" /F /T /IM kai-backend.exe'
  Pop $0
  ; Windows releases file handles asynchronously; overwriting immediately after
  ; the process ends can still hit a lock.
  Sleep 1000
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  DetailPrint "Closing any running Kai backend..."
  nsExec::Exec '"$SYSDIR\taskkill.exe" /F /T /IM kai-backend.exe'
  Pop $0
  Sleep 1000
!macroend
