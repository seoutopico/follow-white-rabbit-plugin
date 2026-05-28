# Follow the White Rabbit - orquestador completo (Windows nativo).
#
# Hace todo el ciclo: investigar 5 temas en paralelo + publicar a GitHub Pages.
# Reemplaza run-research.sh + publish.sh sin necesidad de bash/WSL.
#
# Uso manual:
#   .\cycle.ps1                 # ciclo completo (default)
#   .\cycle.ps1 -DryRun         # solo muestra que haria, sin lanzar workers
#   .\cycle.ps1 -SkipPublish    # investiga pero no publica
#
# Programacion: ver GUIA.md seccion "Automatizacion con Task Scheduler"

[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$SkipPublish,
    [int]$WorkerTimeoutSeconds = 900
)

$ErrorActionPreference = "Stop"

# ProjectDir = wherever the user invoked cycle.ps1 from (their feed project),
# NOT the directory where cycle.ps1 itself lives (the plugin cache).
$ProjectDir = (Get-Location).Path
# ScriptDir = where cycle.ps1 lives (plugin cache). Used only to locate feed.py.
$ScriptDir  = $PSScriptRoot
$FeedPy     = Join-Path $ScriptDir "feed.py"
$ConfigPath = Join-Path $ProjectDir "config.yaml"

# ---------- Logging ----------
$LogsDir = Join-Path $ProjectDir ".logs"
New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null
$RunDate = Get-Date -Format "yyyy-MM-dd"
$RunId = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$MainLog = Join-Path $LogsDir "research-$RunDate.log"

function Log {
    param([string]$msg)
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $msg
    Write-Host $line
    Add-Content -Path $MainLog -Value $line -Encoding UTF8
}

# Limpia logs viejos (>7 dias)
Get-ChildItem $LogsDir -Filter "research-*.log" -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-7) } |
    Remove-Item -Force -ErrorAction SilentlyContinue
Get-ChildItem $LogsDir -Filter "*_round*.log" -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-7) } |
    Remove-Item -Force -ErrorAction SilentlyContinue

Log "=== Ciclo de research: $RunId ==="

# ---------- Sanity checks ----------
foreach ($cmd in @("python", "claude", "git")) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Log "ERROR: '$cmd' no encontrado en PATH. Aborto."
        exit 1
    }
}

if (-not (Test-Path $ConfigPath)) {
    Log "ERROR: config.yaml no encontrado en $ProjectDir."
    Log "       cycle.ps1 debe ejecutarse desde el directorio de tu proyecto"
    Log "       (donde vive config.yaml). Si lo lanzas desde Task Scheduler,"
    Log "       configura WorkingDirectory al directorio del proyecto."
    exit 1
}
if (-not (Test-Path $FeedPy)) {
    Log "ERROR: feed.py no encontrado en $FeedPy. Plugin instalado mal?"
    exit 1
}

# ---------- Parsea topics desde config.yaml ----------
$topicsJson = python -c @"
import json, yaml
with open(r'$ConfigPath', encoding='utf-8') as f:
    cfg = yaml.safe_load(f)
out = []
for t in cfg.get('topics', []):
    out.append({'id': t['id'], 'target': t.get('target', 0), 'model': t.get('model', 'opus')})
print(json.dumps(out))
"@

if ($LASTEXITCODE -ne 0) {
    Log "ERROR: no pude parsear config.yaml. Aborto."
    exit 1
}

$Topics = $topicsJson | ConvertFrom-Json
Log "Topics a procesar: $($Topics.Count)"
foreach ($t in $Topics) {
    Log "  - $($t.id) (target=$($t.target))"
}

if ($DryRun) {
    Log "DRY RUN: termino aqui, no lanzo workers ni publico."
    exit 0
}

# Inicializa feeds (idempotente, crea XMLs que falten).
# Set-Location asegura que feed.py resuelve config.yaml + feeds/ + .state/ en el proyecto del usuario.
Set-Location $ProjectDir
python $FeedPy init | Out-Null

# ---------- Workers en dos fases (scout Sonnet -> writer Opus) ----------
# Por cada topic: primero scout (modelo barato) descubre + JSON de findings,
# luego writer (modelo bueno) lee los findings y escribe. Cada fase es un
# Job independiente. Los topics corren en paralelo dentro de una fase; las
# fases (scout/writer) van en serie por topic.

# Directorio donde el scout deja sus findings JSON (compartido entre fases).
$FindingsRoot = Join-Path $ProjectDir ".state\findings\$RunId"
New-Item -ItemType Directory -Path $FindingsRoot -Force | Out-Null

function Run-Phase {
    param(
        [string]$PhaseName,             # 'scout' o 'writer'
        [string]$AgentName,             # research-scout | research-writer
        [string]$AgentModel,            # sonnet | opus
        [array]$TopicsToRun,            # cada item: .id .target .extraPrompt (opcional) .findingsPath
        [string]$RoundName              # round1 | retry (lo escribe el log)
    )

    $jobs = @()
    foreach ($t in $TopicsToRun) {
        Log "  [$RoundName/$PhaseName] $($t.id) (model=$AgentModel)"

        $promptLines = @()
        if ($PhaseName -eq 'scout') {
            # Recuperar 'recently covered' para que scout filtre antes de buscar
            $covered = ""
            try {
                $stateOut = python $FeedPy state $t.id 2>$null
                if ($LASTEXITCODE -eq 0 -and $stateOut) {
                    $stateText = $stateOut -join "`n"
                    $match = [regex]::Match($stateText, '(?ms)^=== RECENTLY COVERED.*?(?=^===|\z)')
                    if ($match.Success) { $covered = $match.Value.Trim() }
                }
            } catch {}

            $promptLines += "@research-scout Process topic '$($t.id)' with run-id '$RunId'. Your target is $($t.target) findings."
            $promptLines += "Write your findings JSON to: $($t.findingsPath)"
            if ($covered) {
                $promptLines += ""
                $promptLines += $covered
                $promptLines += ""
                $promptLines += "Do NOT include findings about subjects above unless you have genuinely new facts."
            }
            if ($t.extraPrompt) {
                $promptLines += ""
                $promptLines += $t.extraPrompt
            }
        } else {
            # writer
            $promptLines += "@research-writer Process topic '$($t.id)' with run-id '$RunId'."
            $promptLines += "Findings file (already produced by the scout): $($t.findingsPath)"
            $promptLines += "Read it, write each finding as an entry following the topic brief and quality rules, then update knowledge and log."
        }

        $prompt = $promptLines -join "`n"
        $logFile = Join-Path $LogsDir "$($t.id)_${RoundName}_${PhaseName}.log"

        $job = Start-Job -Name "${PhaseName}-$($t.id)" -ScriptBlock {
            param($workDir, $model, $prompt, $logFile, $phase)
            Set-Location $workDir
            # writer needs Write (feed.py add) and Bash; scout needs WebSearch/WebFetch/Write
            $tools = if ($phase -eq 'scout') {
                "WebSearch,WebFetch,Bash,Read,Grep,Glob,Write"
            } else {
                "Bash,Read,Grep,Glob,WebFetch"
            }
            & claude --model $model -p $prompt `
                --allowedTools $tools `
                --permission-mode dontAsk *>&1 | Tee-Object -FilePath $logFile
            return $LASTEXITCODE
        } -ArgumentList $ProjectDir, $AgentModel, $prompt, $logFile, $PhaseName

        $jobs += [pscustomobject]@{ job = $job; topicId = $t.id; logFile = $logFile }
    }

    Log "  [$RoundName/$PhaseName] esperando $($jobs.Count) jobs (timeout: ${WorkerTimeoutSeconds}s)..."

    $deadline = (Get-Date).AddSeconds($WorkerTimeoutSeconds)
    $results = @()
    foreach ($entry in $jobs) {
        $remaining = [int](($deadline - (Get-Date)).TotalSeconds)
        if ($remaining -lt 1) { $remaining = 1 }
        $finished = Wait-Job -Job $entry.job -Timeout $remaining
        $ok = $false
        if (-not $finished) {
            Log "  TIMEOUT: $($entry.topicId)"
            Stop-Job -Job $entry.job -ErrorAction SilentlyContinue
        } else {
            $exit = Receive-Job -Job $entry.job -ErrorAction SilentlyContinue
            if ($entry.job.State -eq "Completed") {
                Log "  OK: $($entry.topicId)"
                $ok = $true
            } else {
                Log "  FAIL: $($entry.topicId) (state=$($entry.job.State))"
            }
        }
        Remove-Job -Job $entry.job -Force -ErrorAction SilentlyContinue
        $results += [pscustomobject]@{ topicId = $entry.topicId; ok = $ok }
    }
    Log "  [$RoundName/$PhaseName] hecho."
    return $results
}

# Orquestador de un round completo: scout -> writer encadenados.
function Spawn-Workers {
    param(
        [string]$RoundName,
        [array]$TopicsToRun     # cada item: .id .target .model .extraPrompt (opcional)
    )

    # Enriquecer cada topic con la ruta de findings que usaran scout y writer
    $enriched = @()
    foreach ($t in $TopicsToRun) {
        $findingsPath = Join-Path $FindingsRoot "$($t.id).json"
        $enriched += [pscustomobject]@{
            id            = $t.id
            target        = $t.target
            model         = $t.model           # legacy: ignorado en este flujo (scout/writer fijan los suyos)
            extraPrompt   = $t.extraPrompt
            findingsPath  = $findingsPath
        }
    }

    # Fase A: scout (Sonnet) en paralelo
    $scoutResults = Run-Phase -PhaseName 'scout' -AgentName 'research-scout' -AgentModel 'sonnet' -TopicsToRun $enriched -RoundName $RoundName

    # Fase B: writer (Opus) en paralelo, SOLO para topics donde el scout escribio el JSON.
    $writeTopics = @()
    foreach ($t in $enriched) {
        $scoutOk = ($scoutResults | Where-Object { $_.topicId -eq $t.id }).ok
        if (-not $scoutOk) {
            Log "  SKIP writer/$($t.id) (scout fallo)"
            continue
        }
        if (-not (Test-Path $t.findingsPath)) {
            Log "  SKIP writer/$($t.id) (scout no genero findings file: $($t.findingsPath))"
            continue
        }
        # Si el JSON tiene findings vacios, tampoco hace falta writer
        try {
            $findingsRaw = Get-Content $t.findingsPath -Raw -Encoding UTF8
            $findingsObj = $findingsRaw | ConvertFrom-Json
            if (-not $findingsObj.findings -or $findingsObj.findings.Count -eq 0) {
                Log "  SKIP writer/$($t.id) (scout devolvio 0 findings: $($findingsObj.skipped_subjects.Count) skipped)"
                continue
            }
        } catch {
            Log "  WARN writer/$($t.id): no pude parsear findings JSON ($_). Lanzo writer igualmente."
        }
        $writeTopics += $t
    }

    if ($writeTopics.Count -eq 0) {
        Log "  [$RoundName] No hay topics con findings para writer. Round terminado."
        return
    }

    Run-Phase -PhaseName 'writer' -AgentName 'research-writer' -AgentModel 'opus' -TopicsToRun $writeTopics -RoundName $RoundName | Out-Null
}

# ---------- Ronda 1: todos los topics ----------
Log "--- Ronda 1: lanzando todos los workers ---"
$round1 = $Topics | ForEach-Object {
    [pscustomobject]@{ id = $_.id; target = $_.target; model = $_.model; extraPrompt = "" }
}
Spawn-Workers -RoundName "round1" -TopicsToRun $round1

# ---------- Check de targets ----------
Log "--- Comprobando targets ---"
$checkOutput = python $FeedPy check-targets --run-id $RunId 2>&1
$checkOutput | ForEach-Object { Log $_ }

$shortfallLine = $checkOutput | Where-Object { $_ -match "__SHORTFALLS_JSON__" } | Select-Object -First 1

if ($shortfallLine) {
    $shortfalls = ($shortfallLine -split "__SHORTFALLS_JSON__:")[1] | ConvertFrom-Json
    if ($shortfalls.Count -gt 0) {
        Log "--- Ronda 2: reintentando topics con shortfall ---"
        $topicMap = @{}
        foreach ($t in $Topics) { $topicMap[$t.id] = $t }
        $retry = @()
        foreach ($s in $shortfalls) {
            $base = $topicMap[$s.topic_id]
            $retry += [pscustomobject]@{
                id = $s.topic_id
                target = $s.gap
                model = $base.model
                extraPrompt = "Previous round produced $($s.added)/$($s.target). Try to produce $($s.gap) more entries by searching different sub-topics and broadening scope. Do NOT re-cover subjects already in state. If you cannot find genuinely new subjects after thorough searching, producing fewer is OK."
            }
        }
        Spawn-Workers -RoundName "retry" -TopicsToRun $retry
        Log "--- Check final ---"
        python $FeedPy check-targets --run-id $RunId 2>&1 | ForEach-Object { Log $_ }
    }
} else {
    Log "Todos los targets se cumplieron en la primera ronda."
}

# ---------- Prune ----------
Log "--- Prune (max 50 entradas/feed) ---"
python $FeedPy prune --keep 50 | ForEach-Object { Log $_ }

# ---------- Publish ----------
if ($SkipPublish) {
    Log "SKIP: --SkipPublish activo, no publico a gh-pages."
    Log "=== Listo (sin publicar) ==="
    exit 0
}

Log "--- Publicando a gh-pages ---"

$baseUrl = python -c @"
import yaml
with open(r'$ConfigPath', encoding='utf-8') as f:
    print(yaml.safe_load(f).get('settings', {}).get('base_url', ''))
"@

if (-not $baseUrl) {
    Log "ERROR: base_url vacio en config.yaml. No publico."
    exit 1
}

# Genera index.html, opml, paginas HTML legibles y archivo cronologico
python $FeedPy index-html --base-url $baseUrl | Out-Null
python $FeedPy opml --base-url $baseUrl | Out-Null
python $FeedPy render-html --base-url $baseUrl | ForEach-Object { Log "  $_" }
python $FeedPy render-archive --base-url $baseUrl | ForEach-Object { Log "  $_" }
python $FeedPy render-readme --base-url $baseUrl | ForEach-Object { Log "  $_" }

# Lock simple para evitar publishes concurrentes
$LockDir = Join-Path $ProjectDir ".publish.lock"
if (Test-Path $LockDir) {
    Log "WARN: $LockDir ya existe. Otro publish podria estar en curso. Salgo."
    exit 0
}
New-Item -ItemType Directory -Path $LockDir -Force | Out-Null

try {
    # Helper: ejecuta un comando git y aborta si exit code != 0.
    # NOTE Windows PowerShell 5.1: cuando se redirige stderr con 2>&1 sobre un
    # ejecutable nativo, cada linea de stderr se envuelve en un ErrorRecord
    # (NativeCommandError). git escribe a stderr cosas informativas no fatales
    # ("Cloning into ...", "To <url>", el resumen del commit), y combinado con
    # el $ErrorActionPreference="Stop" global del script, eso aborta antes de
    # comprobar $LASTEXITCODE. Solucion: aislar el preference para esta llamada,
    # ejecutar con 'Continue', y decidir el fallo solo por el exit code real.
    function Invoke-GitOrFail {
        param([string]$Description, [scriptblock]$Cmd)
        $prev = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            Log "  > $Description"
            $output = & $Cmd 2>&1
            $exit = $LASTEXITCODE
            $output | ForEach-Object { Log "    $_" }
            if ($exit -ne 0) {
                throw "git fallo en '$Description' (exit $exit). Aborto el publish."
            }
        } finally {
            $ErrorActionPreference = $prev
        }
    }

    $remoteUrl = git remote get-url origin
    if ($LASTEXITCODE -ne 0 -or -not $remoteUrl) {
        throw "No pude obtener la URL del remote origin. Aborto el publish."
    }
    Log "Remote origin: $remoteUrl"

    $workDir = Join-Path ([System.IO.Path]::GetTempPath()) ("ccfeed-pub-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
    $cloneDir = Join-Path $workDir "repo"

    # Detecta si gh-pages existe en remoto
    & git ls-remote --exit-code --heads $remoteUrl gh-pages 2>$null | Out-Null
    $hasGhPages = ($LASTEXITCODE -eq 0)

    if ($hasGhPages) {
        Log "Rama gh-pages existe en remoto. Clonando..."
        Invoke-GitOrFail "git clone --branch gh-pages" {
            git clone --depth 1 --single-branch --branch gh-pages $remoteUrl $cloneDir
        }
        if (-not (Test-Path (Join-Path $cloneDir ".git"))) {
            throw "Clone aparentemente OK pero $cloneDir/.git no existe. Aborto."
        }
    } else {
        Log "Rama gh-pages no existe en remoto. Creandola desde cero..."
        New-Item -ItemType Directory -Path $cloneDir -Force | Out-Null
        # git init con la rama inicial directamente como gh-pages (Git 2.28+)
        Invoke-GitOrFail "git init -b gh-pages" {
            git init -b gh-pages $cloneDir
        }
        Invoke-GitOrFail "git remote add origin" {
            git -C $cloneDir remote add origin $remoteUrl
        }
        # Sanity check: confirma que estamos en la rama gh-pages
        $currentBranch = (& git -C $cloneDir symbolic-ref --short HEAD 2>$null)
        if ($currentBranch -ne "gh-pages") {
            throw "Tras git init, esperaba estar en rama 'gh-pages' pero estoy en '$currentBranch'. Aborto."
        }
        Log "  OK rama inicial = gh-pages"
    }

    # Limpia el clone (excepto .git) y copia los feeds nuevos
    Log "Limpiando $cloneDir y copiando feeds..."
    Get-ChildItem $cloneDir -Force -ErrorAction Stop | Where-Object { $_.Name -ne ".git" } |
        Remove-Item -Recurse -Force -ErrorAction Stop

    $FeedsDir = Join-Path $ProjectDir "feeds"
    $copiedCount = 0
    # Top-level files (xml, html, opml, md, png) — anything but the lock dir/files.
    Get-ChildItem $FeedsDir -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Extension -notin @('.lock') -and $_.Name -ne '.gitkeep' } |
        ForEach-Object { Copy-Item $_.FullName $cloneDir -Force; $copiedCount++ }
    # Subdirectories topics/ and archive/ — copy recursively, keep structure.
    foreach ($sub in @('topics', 'archive')) {
        $src = Join-Path $FeedsDir $sub
        if (Test-Path $src) {
            Copy-Item $src $cloneDir -Recurse -Force
            $copiedCount += (Get-ChildItem $src -Recurse -File | Measure-Object).Count
        }
    }
    Log "  Copiados $copiedCount archivos a $cloneDir"
    if ($copiedCount -eq 0) {
        throw "No se copio ningun archivo a $cloneDir - algo esta mal con $FeedsDir. Aborto."
    }

    Push-Location $cloneDir
    try {
        Invoke-GitOrFail "git add -A" { git add -A }

        # ?Hay cambios?
        & git diff --cached --quiet
        $hasChanges = ($LASTEXITCODE -ne 0)

        if ($hasChanges) {
            $msg = "Update feeds {0}" -f (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
            Invoke-GitOrFail "git commit" {
                git -c user.name="follow-white-rabbit" -c user.email="bot@seoutopico.local" commit -m $msg
            }
            # Primer push de la rama nueva necesita -u; los siguientes no, pero -u es idempotente
            Invoke-GitOrFail "git push origin gh-pages" {
                git push -u origin gh-pages
            }
            Log "OK publicado a gh-pages."
        } else {
            Log "No hay cambios que publicar."
        }
    } finally {
        Pop-Location
    }

    # Ping WebSub si esta configurado
    $websub = python -c @"
import yaml
with open(r'$ConfigPath', encoding='utf-8') as f:
    print(yaml.safe_load(f).get('settings', {}).get('websub_hub', ''))
"@

    if ($websub) {
        Log "Pingueando WebSub hub: $websub"
        $xmls = Get-ChildItem $FeedsDir -Filter "*.xml"
        foreach ($x in $xmls) {
            $feedUrl = "$($baseUrl.TrimEnd('/'))/$($x.Name)"
            try {
                $resp = Invoke-WebRequest -Uri $websub -Method Post -Body @{ "hub.mode" = "publish"; "hub.url" = $feedUrl } -UseBasicParsing -TimeoutSec 10
                Log "  OK $($x.Name) ($($resp.StatusCode))"
            } catch {
                Log "  WARN ping fallo para $($x.Name): $_"
            }
        }
    }

    Remove-Item $workDir -Recurse -Force -ErrorAction SilentlyContinue
} finally {
    Remove-Item $LockDir -Recurse -Force -ErrorAction SilentlyContinue
}

Log "=== Ciclo completo ==="
