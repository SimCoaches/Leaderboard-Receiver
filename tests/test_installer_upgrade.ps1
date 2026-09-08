$ErrorActionPreference = 'Stop'

$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$tempRoot = [IO.Path]::GetFullPath((Join-Path $repoRoot '.codex-temp\installer-upgrade-test'))
$repoPrefix = $repoRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar

if (-not $tempRoot.StartsWith($repoPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to use a test directory outside the repository: $tempRoot"
}

$testScript = Join-Path $tempRoot 'installer-upgrade-test.iss'
$buildDir = Join-Path $tempRoot 'build'
$installDir = Join-Path $tempRoot 'existing-install'
$iscc = 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'

try {
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Path $buildDir, $installDir -Force | Out-Null

    $sentinels = [ordered]@{
        'config.json' = '{"ranking_mode":"cars_passed","background_image":"customer-logo.png"}'
        'cars_passed.csv' = "driver_name,cars_passed,finishing_position,lap_time,updated_at`r`nJosh Existing,12,28,88.125,2026-09-08T10:00:00`r`n"
        'lap_times.csv' = "simulator_id,driver_name,lap_time,email,phone,session_id,timestamp,distance_pct,survey_answers`r`n1,Existing Sector Driver,125.500,,,old-session,2026-09-07T10:00:00,93.75,{}`r`n"
        'queue.json' = '{"queue":[{"name":"Existing Guest"}]}'
        'sms_config.json' = '{"enabled":false,"provider":"twilio"}'
        'customer-logo.png' = 'existing-customer-branding'
    }
    foreach ($entry in $sentinels.GetEnumerator()) {
        [IO.File]::WriteAllText((Join-Path $installDir $entry.Key), $entry.Value, [Text.UTF8Encoding]::new($false))
    }

    $installerSource = [IO.File]::ReadAllText((Join-Path $repoRoot 'installer.iss'))
    $installerSource = $installerSource.Replace(
        "[Setup]`r`n",
        "[Setup]`r`nSourceDir=$repoRoot`r`n"
    ).Replace(
        'OutputDir=Output',
        "OutputDir=$buildDir"
    ).Replace(
        'OutputBaseFilename=LapTimeReceiver_Setup',
        'OutputBaseFilename=LapTimeReceiver_UpgradeTest'
    ).Replace(
        'PrivilegesRequired=admin',
        "PrivilegesRequired=lowest`r`nUninstallable=no"
    )
    [IO.File]::WriteAllText($testScript, $installerSource, [Text.UTF8Encoding]::new($false))

    & $iscc $testScript | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "The upgrade-test installer failed to compile (exit $LASTEXITCODE)."
    }

    $testInstaller = Join-Path $buildDir 'LapTimeReceiver_UpgradeTest.exe'
    $installProcess = Start-Process -FilePath $testInstaller -ArgumentList @(
        '/VERYSILENT',
        '/SUPPRESSMSGBOXES',
        '/NORESTART',
        '/SP-',
        '/NOICONS',
        "/DIR=`"$installDir`""
    ) -WindowStyle Hidden -Wait -PassThru
    if ($installProcess.ExitCode -ne 0) {
        throw "The upgrade-test installer failed (exit $($installProcess.ExitCode))."
    }

    foreach ($entry in $sentinels.GetEnumerator()) {
        $actual = [IO.File]::ReadAllText((Join-Path $installDir $entry.Key), [Text.Encoding]::UTF8)
        if ($actual -cne $entry.Value) {
            throw "Existing file was changed during upgrade: $($entry.Key)"
        }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $installDir 'Lap Time Receiver.exe'))) {
        throw 'The upgraded Receiver executable was not installed.'
    }

    Write-Host 'PASS: in-place upgrade preserved manual results, lap/distance history, settings, queue, SMS settings, and branding.'
}
finally {
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force
    }
}
