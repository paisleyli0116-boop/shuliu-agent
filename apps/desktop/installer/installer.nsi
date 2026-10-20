!macro customInstall
  IfFileExists "$INSTDIR\resources\python\ShuLiuRuntime.bin" 0 runtime_ensure_payload
  Delete "$INSTDIR\resources\python\ShuLiuRuntime.exe"
  Rename "$INSTDIR\resources\python\ShuLiuRuntime.bin" "$INSTDIR\resources\python\ShuLiuRuntime.exe"
  runtime_ensure_payload:
  IfFileExists "$INSTDIR\resources\python\ShuLiuRuntime.exe" runtime_payload_present runtime_payload_missing
  runtime_payload_missing:
  Abort "The required ShuLiu Python runtime is missing."
  runtime_payload_present:
  ClearErrors
  FileOpen $0 "$INSTDIR\install.log" a
  IfErrors install_log_unavailable
  FileWrite $0 "sidecar_payload=installed$\r$\n"
  FileClose $0
  install_log_unavailable:
  DetailPrint "ShuLiu Python runtime installed."
!macroend

!macro customUnInstall
  DetailPrint "Per-user configuration and runtime data are preserved."
!macroend
