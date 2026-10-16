param([Parameter(Mandatory=$true)][string]$PlanPath)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$plan=Get-Content -LiteralPath $PlanPath -Raw -Encoding UTF8 | ConvertFrom-Json
if($plan.prefix -notmatch '^QQMCP-AI-Temp-[a-f0-9]{12}$' -or $plan.task_path -ne '\QQMCP\'){throw 'Invalid QQ cleanup task scope'}
$root=[IO.Path]::GetFullPath($plan.root)
if([IO.Path]::GetFullPath($plan.script) -ne (Join-Path $root 'windows_image_cleanup.py')){throw 'Unexpected task runner'}
$desired=@($plan.tasks | ForEach-Object { $_.name })
if($desired.Count -gt 0){
    $service=New-Object -ComObject 'Schedule.Service'
    $service.Connect()
    try{$null=$service.GetFolder('\QQMCP')}catch{$null=$service.GetFolder('\').CreateFolder('QQMCP')}
}
$existing=@(Get-ScheduledTask -TaskPath $plan.task_path -ErrorAction SilentlyContinue | Where-Object { $_.TaskName -match ('^'+[regex]::Escape($plan.prefix)+'-\d+$') })
$user=[Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal=New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -Hidden
foreach($item in $plan.tasks){
    if($item.name -ne ($plan.prefix+'-'+[string]$item.group) -or $item.group -le 0){throw 'Invalid task name'}
    if($item.interval -lt 1 -or $item.interval -gt 10080 -or $item.time -notmatch '^(?:[01]\d|2[0-3]):[0-5]\d$'){throw 'Invalid cleanup schedule'}
    $action=New-ScheduledTaskAction -Execute $plan.python -Argument ('"'+$plan.script+'" --run --group '+[string]$item.group) -WorkingDirectory $root
    if($item.schedule -eq 'daily'){
        $trigger=New-ScheduledTaskTrigger -Daily -At $item.time
        $chinaNow=[DateTimeOffset]::UtcNow.ToOffset([TimeSpan]::FromHours(8))
        $trigger.StartBoundary=$chinaNow.ToString('yyyy-MM-dd')+'T'+$item.time+':00+08:00'
    }elseif($item.schedule -eq 'interval'){
        $trigger=New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval ([TimeSpan]::FromMinutes($item.interval))
    }else{throw 'Unknown Windows schedule'}
    Register-ScheduledTask -TaskName $item.name -TaskPath $plan.task_path -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description ('QQ Bot AI image temp cleanup for group '+$item.group+'; '+$root) -Force | Out-Null
    if($item.enabled -eq $false){Disable-ScheduledTask -TaskName $item.name -TaskPath $plan.task_path | Out-Null}
}
foreach($item in $existing){
    if($item.TaskName -notin $desired){Unregister-ScheduledTask -TaskName $item.TaskName -TaskPath $plan.task_path -Confirm:$false}
}
Write-Output ('QQ AI cleanup tasks synchronized: '+$desired.Count)
