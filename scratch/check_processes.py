import subprocess

cmd = ["powershell", "-NoProfile", "-Command", "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*provider_agent*' -or $_.CommandLine -like '*uvicorn*' } | Select-Object ProcessId, CommandLine | Format-List"]
res = subprocess.run(cmd, capture_output=True, text=True)
print(res.stdout)
