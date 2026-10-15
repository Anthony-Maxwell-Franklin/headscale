param(
    [string]$RepoPath = "headscale",
    [string]$OutputMarkdown = "headscale_metric_analysis.md",
    [string]$RepoOwner = "",
    [string]$RepoName = "",
    [string]$GitHubToken = $env:GITHUB_TOKEN,
    [string]$StableTagUpperBound = "v0.28.0"
)

$ErrorActionPreference = "Stop"

$scriptBase = $PSScriptRoot

function Resolve-PathFromScriptBase {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$BaseDir
    )

    if ([System.IO.Path]::IsPathRooted($Path)) {
        return [System.IO.Path]::GetFullPath($Path)
    }

    return [System.IO.Path]::GetFullPath((Join-Path -Path $BaseDir -ChildPath $Path))
}

$RepoPath = Resolve-PathFromScriptBase -Path $RepoPath -BaseDir $scriptBase
$OutputMarkdown = Resolve-PathFromScriptBase -Path $OutputMarkdown -BaseDir $scriptBase

function Resolve-GitHubRepoFromOrigin {
    param([string]$Repo)

    $allRemotes = @("upstream", "origin")
    $discovered = @(git -C "$Repo" remote 2>$null)
    foreach ($name in $discovered) {
        if ([string]::IsNullOrWhiteSpace($name)) { continue }
        if ($allRemotes -notcontains $name.Trim()) {
            $allRemotes += $name.Trim()
        }
    }

    foreach ($remoteName in $allRemotes) {
        $url = git -C "$Repo" remote get-url $remoteName 2>$null
        if ([string]::IsNullOrWhiteSpace($url)) {
            continue
        }

        if ($url -match 'github\.com[:/](?<owner>[^/]+)/(?<repo>[^/.]+)(?:\.git)?$') {
            return [PSCustomObject]@{
                Owner = [string]$matches['owner']
                Repo = [string]$matches['repo']
            }
        }
    }

    return $null
}

if ([string]::IsNullOrWhiteSpace($RepoOwner) -or [string]::IsNullOrWhiteSpace($RepoName)) {
    $repoInfo = Resolve-GitHubRepoFromOrigin -Repo $RepoPath
    if ($null -ne $repoInfo) {
        if ([string]::IsNullOrWhiteSpace($RepoOwner)) {
            $RepoOwner = $repoInfo.Owner
        }
        if ([string]::IsNullOrWhiteSpace($RepoName)) {
            $RepoName = $repoInfo.Repo
        }
    }
}

if ([string]::IsNullOrWhiteSpace($RepoOwner) -or [string]::IsNullOrWhiteSpace($RepoName)) {
    throw "Unable to resolve GitHub owner/repo from .git origin. Pass -RepoOwner and -RepoName explicitly."
}

function Get-StableTagMetadata {
    param(
        [string]$Repo,
        [string]$UpperBoundTag
    )

    function Convert-StableTagToVersion {
        param([string]$Tag)

        if ([string]::IsNullOrWhiteSpace($Tag)) {
            return $null
        }

        $trimmed = $Tag.Trim()
        if ($trimmed.StartsWith('v') -or $trimmed.StartsWith('V')) {
            $trimmed = $trimmed.Substring(1)
        }

        try {
            return [version]$trimmed
        }
        catch {
            return $null
        }
    }

    $stableTags = @(git -C "$Repo" tag --list "v*" --sort=version:refname | Where-Object { $_ -and $_ -notmatch '-' })
    if (-not [string]::IsNullOrWhiteSpace($UpperBoundTag)) {
        $upperVersion = Convert-StableTagToVersion -Tag $UpperBoundTag
        if ($null -eq $upperVersion) {
            throw "StableTagUpperBound '$UpperBoundTag' is not a valid stable semantic version tag."
        }

        $stableTags = @(
            $stableTags |
            Where-Object {
                $v = Convert-StableTagToVersion -Tag $_
                $null -ne $v -and $v -le $upperVersion
            }
        )

        if ($stableTags -notcontains $UpperBoundTag) {
            throw "StableTagUpperBound '$UpperBoundTag' was not found among stable tags in '$Repo'."
        }
    }

    if ($stableTags.Count -lt 5) {
        throw "At least 5 stable tags are required to build five-phase windows."
    }

    $rows = New-Object System.Collections.Generic.List[object]
    $idx = 0
    foreach ($tag in $stableTags) {
        $rawTs = git -C "$Repo" log -1 --date=unix --pretty=format:%at $tag 2>$null
        if ([string]::IsNullOrWhiteSpace($rawTs)) {
            continue
        }

        $ts = [int64]$rawTs.Trim()
        $dateUtc = [DateTimeOffset]::FromUnixTimeSeconds($ts).UtcDateTime
        $rows.Add([PSCustomObject]@{
                Index = $idx
                Tag = $tag
                DateUtc = $dateUtc
                Year = $dateUtc.Year
            })
        $idx++
    }

    if ($rows.Count -lt 5) {
        throw "Unable to resolve at least 5 stable tags with valid commit dates."
    }

    return $rows.ToArray()
}

function Get-LastTagInYear {
    param(
        [object[]]$TagRows,
        [int]$Year
    )

    $hits = @($TagRows | Where-Object { $_.Year -eq $Year })
    if ($hits.Count -eq 0) {
        return $null
    }

    return $hits[$hits.Count - 1]
}

function Get-FivePhaseAnchors {
    param([object[]]$TagRows)

    if ($TagRows.Count -lt 5) {
        throw "Cannot build five phases from fewer than 5 stable tags."
    }

    $start = $TagRows[0]
    $current = $TagRows[$TagRows.Count - 1]
    $baseYear = [int]$start.Year

    $early = Get-LastTagInYear -TagRows $TagRows -Year $baseYear
    $expansion = Get-LastTagInYear -TagRows $TagRows -Year ($baseYear + 1)
    $maturity = Get-LastTagInYear -TagRows $TagRows -Year ($baseYear + 2)

    if ($null -eq $early -or $null -eq $expansion -or $null -eq $maturity) {
        throw "Unable to derive five phase anchors from stable tags by year boundaries."
    }

    if ($start.Index -ge $early.Index -or $early.Index -ge $expansion.Index -or $expansion.Index -ge $maturity.Index -or $maturity.Index -ge $current.Index) {
        throw "Derived anchors are not strictly increasing."
    }

    return [PSCustomObject]@{
        Start = $start
        Early = $early
        Expansion = $expansion
        Maturity = $maturity
        Current = $current
    }
}

$stableTagRows = Get-StableTagMetadata -Repo $RepoPath -UpperBoundTag $StableTagUpperBound
$anchors = Get-FivePhaseAnchors -TagRows $stableTagRows

$windows = @(
    [PSCustomObject]@{ Stage = "Start"; Anchor = $anchors.Start.Tag; CumRange = $anchors.Start.Tag; SegRange = $anchors.Start.Tag },
    [PSCustomObject]@{ Stage = "Early growth"; Anchor = $anchors.Early.Tag; CumRange = "$($anchors.Start.Tag)..$($anchors.Early.Tag)"; SegRange = "$($anchors.Start.Tag)..$($anchors.Early.Tag)" },
    [PSCustomObject]@{ Stage = "Expansion"; Anchor = $anchors.Expansion.Tag; CumRange = "$($anchors.Start.Tag)..$($anchors.Expansion.Tag)"; SegRange = "$($anchors.Early.Tag)..$($anchors.Expansion.Tag)" },
    [PSCustomObject]@{ Stage = "Maturity"; Anchor = $anchors.Maturity.Tag; CumRange = "$($anchors.Start.Tag)..$($anchors.Maturity.Tag)"; SegRange = "$($anchors.Expansion.Tag)..$($anchors.Maturity.Tag)" },
    [PSCustomObject]@{ Stage = "Current"; Anchor = $anchors.Current.Tag; CumRange = "$($anchors.Start.Tag)..$($anchors.Current.Tag)"; SegRange = "$($anchors.Maturity.Tag)..$($anchors.Current.Tag)" }
)

$anchorDateMap = @{}
foreach ($row in $stableTagRows) {
    $anchorDateMap[$row.Tag] = $row.DateUtc
}

$fixPattern = [regex]'(?i)\b(fix|bug|panic|regression|hotfix|repair|broken|flaky|failure|failing|error)\b'

function Get-Median {
    param([double[]]$Values)
    if (-not $Values -or $Values.Count -eq 0) { return $null }
    $sorted = $Values | Sort-Object
    $n = $sorted.Count
    if ($n % 2 -eq 1) {
        return [double]$sorted[[int]($n / 2)]
    }
    return ([double]$sorted[$n / 2 - 1] + [double]$sorted[$n / 2]) / 2.0
}

function Get-P75 {
    param([double[]]$Values)
    if (-not $Values -or $Values.Count -eq 0) { return $null }
    $sorted = $Values | Sort-Object
    $n = $sorted.Count
    $idx = [Math]::Ceiling(0.75 * $n) - 1
    if ($idx -lt 0) { $idx = 0 }
    return [double]$sorted[[int]$idx]
}

function Get-Percentile {
    param(
        [double[]]$Values,
        [double]$P
    )

    if (-not $Values -or $Values.Count -eq 0) { return $null }

    $sorted = $Values | Sort-Object
    $n = $sorted.Count

    if ($n -eq 1) {
        return [double]$sorted[0]
    }

    $rank = ($n - 1) * $P
    $lower = [int][Math]::Floor($rank)
    $upper = [int][Math]::Ceiling($rank)

    if ($lower -eq $upper) {
        return [double]$sorted[$lower]
    }

    $weight = $rank - $lower
    return [double]$sorted[$lower] + ([double]$sorted[$upper] - [double]$sorted[$lower]) * $weight
}

function Parse-CommitsForRange {
    param(
        [string]$Repo,
        [string]$RangeSpec
    )

    $lines = git -C "$Repo" log --no-merges --date=unix --pretty=format:'@@@%H|%an|%at|%s' --name-only $RangeSpec

    $commits = New-Object System.Collections.Generic.List[object]
    $current = $null

    foreach ($line in $lines) {
        if ($line.StartsWith('@@@')) {
            if ($null -ne $current) {
                $commits.Add([PSCustomObject]$current)
            }

            $parts = $line.Substring(3).Split('|', 4)
            $current = [ordered]@{
                Hash = $parts[0]
                Author = $parts[1]
                Timestamp = [int64]$parts[2]
                Subject = $parts[3]
                Files = (New-Object System.Collections.Generic.List[string])
            }
            continue
        }

        if ($null -ne $current -and $line.Trim() -ne '') {
            $current.Files.Add($line.Trim())
        }
    }

    if ($null -ne $current) {
        $commits.Add([PSCustomObject]$current)
    }

    return @($commits | Where-Object { $_.Author -notmatch '\[bot\]' })
}

function Get-NextLinkFromHeader {
    param([string]$LinkHeader)

    if ([string]::IsNullOrWhiteSpace($LinkHeader)) {
        return $null
    }

    foreach ($segment in ($LinkHeader -split ',')) {
        $s = $segment.Trim()
        if ($s -match '<([^>]+)>;\s*rel="next"') {
            return [string]$matches[1]
        }
    }

    return $null
}

function Get-BugIssueMttrRows {
    param(
        [string]$Owner,
        [string]$Repo,
        [string]$Token,
        [datetime]$ClosedUntilUtc
    )

    if ([string]::IsNullOrWhiteSpace($Token)) {
        throw "GitHub token is required for integrated issue fetching."
    }

    $headers = @{
        "User-Agent" = "headscale-m4b-fetch"
        "Accept" = "application/vnd.github+json"
        "X-GitHub-Api-Version" = "2022-11-28"
        "Authorization" = "Bearer $Token"
    }

    $url = "https://api.github.com/repos/$Owner/$Repo/issues?state=closed&labels=bug&sort=created&direction=asc&per_page=100"
    $results = New-Object System.Collections.Generic.List[object]

    while (-not [string]::IsNullOrWhiteSpace($url)) {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $url -Headers $headers
        $items = @($response.Content | ConvertFrom-Json)

        foreach ($issue in $items) {
            if ($null -ne $issue.pull_request) {
                continue
            }

            if ($null -eq $issue.created_at -or $null -eq $issue.closed_at) {
                continue
            }

            $created = [DateTimeOffset]::Parse([string]$issue.created_at).UtcDateTime
            $closed = [DateTimeOffset]::Parse([string]$issue.closed_at).UtcDateTime
            if ($closed -gt $ClosedUntilUtc) {
                continue
            }

            $mttrDays = ($closed - $created).TotalDays
            if ($mttrDays -lt 0) {
                continue
            }

            $results.Add([PSCustomObject]@{
                ClosedAt = $closed
                ClosedYear = $closed.Year
                MTTRDays = [double]$mttrDays
            })
        }

        $linkHeader = $response.Headers["Link"]
        if ($linkHeader -is [System.Array]) {
            $linkHeader = $linkHeader[0]
        }
        $url = Get-NextLinkFromHeader -LinkHeader ([string]$linkHeader)
    }

    return $results.ToArray()
}

function Get-M4ProxyStats {
    param(
        [object[]]$Commits,
        [regex]$FixPattern
    )

    $repairLags = New-Object System.Collections.Generic.List[double]
    $fileLastTouch = @{}

    foreach ($commit in ($Commits | Sort-Object Timestamp)) {
        $uniqueFiles = @($commit.Files | Sort-Object -Unique)
        if ($uniqueFiles.Count -eq 0) { continue }

        if ($FixPattern.IsMatch($commit.Subject)) {
            $previousTouches = New-Object System.Collections.Generic.List[double]
            foreach ($file in $uniqueFiles) {
                if ($fileLastTouch.ContainsKey($file)) {
                    $previousTouches.Add([double]$fileLastTouch[$file])
                }
            }

            if ($previousTouches.Count -gt 0) {
                $latestPriorTs = ($previousTouches | Measure-Object -Maximum).Maximum
                $lagDays = [double](($commit.Timestamp - $latestPriorTs) / 86400.0)
                if ($lagDays -ge 0) { $repairLags.Add($lagDays) }
            }
        }

        foreach ($file in $uniqueFiles) {
            $fileLastTouch[$file] = [double]$commit.Timestamp
        }
    }

    $samples = $repairLags.Count
    $mean = $null
    $median = $null
    $p75 = $null
    $p90 = $null

    if ($samples -gt 0) {
        $mean = [Math]::Round(([double](($repairLags | Measure-Object -Average).Average)), 2)
        $median = [Math]::Round((Get-Percentile -Values $repairLags.ToArray() -P 0.5), 2)
        $p75 = [Math]::Round((Get-Percentile -Values $repairLags.ToArray() -P 0.75), 2)
        $p90 = [Math]::Round((Get-Percentile -Values $repairLags.ToArray() -P 0.9), 2)
    }

    return [PSCustomObject]@{
        Samples = $samples
        Mean = $mean
        Median = $median
        P75 = $p75
        P90 = $p90
    }
}

$results = New-Object System.Collections.Generic.List[object]

foreach ($window in $windows) {
    $commitsM12 = Parse-CommitsForRange -Repo $RepoPath -RangeSpec $window.CumRange
    $commitsM34 = Parse-CommitsForRange -Repo $RepoPath -RangeSpec $window.SegRange

    # M1: Hotspot Risk (churn concentration by file)
    $fileTouches = @{}
    foreach ($commit in $commitsM12) {
        $uniqueFiles = @($commit.Files | Sort-Object -Unique)
        foreach ($file in $uniqueFiles) {
            if (-not $fileTouches.ContainsKey($file)) { $fileTouches[$file] = 0 }
            $fileTouches[$file]++
        }
    }

    $touchTotal = 0
    foreach ($value in $fileTouches.Values) { $touchTotal += [int]$value }

    $topFiles = @($fileTouches.GetEnumerator() | Sort-Object Value -Descending)
    $top1Touches = if ($topFiles.Count -gt 0) { [int]$topFiles[0].Value } else { 0 }
    $top10TouchSum = 0
    $limit = [Math]::Min(10, $topFiles.Count)
    for ($i = 0; $i -lt $limit; $i++) { $top10TouchSum += [int]$topFiles[$i].Value }

    $top10Share = if ($touchTotal -gt 0) { [Math]::Round(100.0 * $top10TouchSum / $touchTotal, 2) } else { 0 }
    $top1Share = if ($touchTotal -gt 0) { [Math]::Round(100.0 * $top1Touches / $touchTotal, 2) } else { 0 }
    $hotspotHHI = 0.0
    if ($touchTotal -gt 0) {
        foreach ($touchCount in $fileTouches.Values) {
            $share = [double]$touchCount / [double]$touchTotal
            $hotspotHHI += ($share * $share)
        }
    }
    $hotspotHHI = [Math]::Round($hotspotHHI, 4)

    $top3 = @()
    for ($i = 0; $i -lt [Math]::Min(3, $topFiles.Count); $i++) {
        $top3 += "{0} ({1})" -f $topFiles[$i].Key, $topFiles[$i].Value
    }

    # M2: Knowledge Concentration (author concentration)
    $authorGroups = @($commitsM12 | Group-Object Author | Sort-Object Count -Descending)
    $totalCommitsM12 = $commitsM12.Count
    $authorCountM12 = $authorGroups.Count

    $top1CommitCount = if ($authorGroups.Count -gt 0) { $authorGroups[0].Count } else { 0 }
    $top2CommitCount = if ($authorGroups.Count -gt 1) { $authorGroups[0].Count + $authorGroups[1].Count } elseif ($authorGroups.Count -eq 1) { $authorGroups[0].Count } else { 0 }

    $top1CommitShare = if ($totalCommitsM12 -gt 0) { [Math]::Round(100.0 * $top1CommitCount / $totalCommitsM12, 2) } else { 0 }
    $top2CommitShare = if ($totalCommitsM12 -gt 0) { [Math]::Round(100.0 * $top2CommitCount / $totalCommitsM12, 2) } else { 0 }

    $hhi = 0.0
    foreach ($group in $authorGroups) {
        $share = $group.Count / $totalCommitsM12
        $hhi += ($share * $share)
    }
    $hhi = [Math]::Round($hhi, 4)
    $effectiveAuthors = if ($hhi -gt 0) { [Math]::Round(1.0 / $hhi, 2) } else { 0 }

    $cumCommits = 0
    $busFactor50 = 0
    foreach ($group in $authorGroups) {
        $busFactor50++
        $cumCommits += $group.Count
        if ($cumCommits -ge 0.5 * $totalCommitsM12) { break }
    }

    # M3: Coordination Latency (cross-author handoff latency)
    $fileEvents = @{}
    foreach ($commit in $commitsM34) {
        $uniqueFiles = @($commit.Files | Sort-Object -Unique)
        foreach ($file in $uniqueFiles) {
            if (-not $fileEvents.ContainsKey($file)) {
                $fileEvents[$file] = New-Object System.Collections.Generic.List[object]
            }
            $fileEvents[$file].Add([PSCustomObject]@{
                Timestamp = $commit.Timestamp
                Author = $commit.Author
            })
        }
    }

    $transitions = 0
    $handoffs = 0
    $handoffLags = New-Object System.Collections.Generic.List[double]

    foreach ($kv in $fileEvents.GetEnumerator()) {
        $events = @($kv.Value | Sort-Object Timestamp)
        if ($events.Count -lt 2) { continue }

        for ($i = 1; $i -lt $events.Count; $i++) {
            $transitions++
            if ($events[$i].Author -ne $events[$i - 1].Author) {
                $handoffs++
                $lagDays = [double](($events[$i].Timestamp - $events[$i - 1].Timestamp) / 86400.0)
                if ($lagDays -ge 0) { $handoffLags.Add($lagDays) }
            }
        }
    }

    $handoffRate = if ($transitions -gt 0) { [Math]::Round(100.0 * $handoffs / $transitions, 2) } else { 0 }
    $medianHandoffDays = Get-Median -Values $handoffLags.ToArray()
    $p75HandoffDays = Get-P75 -Values $handoffLags.ToArray()
    if ($null -ne $medianHandoffDays) { $medianHandoffDays = [Math]::Round($medianHandoffDays, 2) }
    if ($null -ne $p75HandoffDays) { $p75HandoffDays = [Math]::Round($p75HandoffDays, 2) }

    # M4: Mean Time to Repair (file-touch lag proxy)
    # For each fix-like commit, use time since the latest previous touch on the same changed files.
    $m4PhaseStats = Get-M4ProxyStats -Commits $commitsM34 -FixPattern $fixPattern
    $mttrSamples = $m4PhaseStats.Samples
    $mttrMeanDays = $m4PhaseStats.Mean
    $mttrMedianDays = $m4PhaseStats.Median
    $mttrP75Days = $m4PhaseStats.P75

    $results.Add([PSCustomObject]@{
        Stage = $window.Stage
        Anchor = $window.Anchor
        CumRange = $window.CumRange
        SegRange = $window.SegRange
        M12Commits = $totalCommitsM12
        M12Authors = $authorCountM12

        M1Touches = $touchTotal
        M1Top10Share = $top10Share
        M1Top1Share = $top1Share
        M1HHI = $hotspotHHI
        M1Top3Files = ($top3 -join '; ')

        M2Top1Share = $top1CommitShare
        M2Top2Share = $top2CommitShare
        M2HHI = $hhi
        M2EffectiveAuthors = $effectiveAuthors
        M2BusFactor50 = $busFactor50

        M3HandoffRate = $handoffRate
        M3MedianHandoffDays = $medianHandoffDays
        M3P75HandoffDays = $p75HandoffDays

        M4Samples = $mttrSamples
        M4MTTRMeanDays = $mttrMeanDays
        M4MTTRMedianDays = $mttrMedianDays
        M4MTTRP75Days = $mttrP75Days
    })
}

$bugIssuePhaseStats = @()
$currentAnchorUtc = $anchors.Current.DateUtc
$bugIssues = Get-BugIssueMttrRows -Owner $RepoOwner -Repo $RepoName -Token $GitHubToken -ClosedUntilUtc $currentAnchorUtc

if ($bugIssues.Count -gt 0) {
    for ($i = 0; $i -lt $windows.Count; $i++) {
        $window = $windows[$i]
        $upperDate = $anchorDateMap[$window.Anchor]
        if ($null -eq $upperDate) {
            throw "Missing anchor date for tag $($window.Anchor)."
        }

        $lowerAnchor = if ($i -gt 0) { [string]$windows[$i - 1].Anchor } else { $null }
        $lowerDate = if ($i -gt 0) { $anchorDateMap[$lowerAnchor] } else { $null }

        $phaseValues = @(
            $bugIssues |
            Where-Object {
                $_.ClosedAt -le $upperDate -and (
                    $null -eq $lowerDate -or $_.ClosedAt -gt $lowerDate
                )
            } |
            ForEach-Object { [double]$_.MTTRDays }
        )

        $phaseCount = $phaseValues.Count
        $phaseMean = $null
        $phaseMedian = $null
        $phaseP75 = $null

        if ($phaseCount -gt 0) {
            $phaseMean = [Math]::Round(([double](($phaseValues | Measure-Object -Average).Average)), 2)
            $phaseMedian = [Math]::Round((Get-Percentile -Values $phaseValues -P 0.5), 2)
            $phaseP75 = [Math]::Round((Get-Percentile -Values $phaseValues -P 0.75), 2)
        }

        $bugIssuePhaseStats += [PSCustomObject]@{
            Stage = $window.Stage
            Anchor = $window.Anchor
            Count = $phaseCount
            Mean = $phaseMean
            Median = $phaseMedian
            P75 = $phaseP75
        }
    }
}

function Format-Nullable {
    param($Value)
    if ($null -eq $Value) { return "N/A" }
    return "$Value"
}

$md = New-Object System.Collections.Generic.List[string]
$md.Add("# Headscale Metrics Analysis (for Report M1-M4)")
$md.Add("")
$md.Add("## Window Definition")
$md.Add("")
$md.Add("| Stage | Anchor | Cumulative Range (M1/M2) | Segmented Range (M3/M4) |")
$md.Add("|---|---|---|---|")
foreach ($row in $results) {
    $md.Add("| $($row.Stage) | $($row.Anchor) | $($row.CumRange) | $($row.SegRange) |")
}

$md.Add("")
$md.Add("## M1 Hotspot Concentration (HHI)")
$md.Add("")
$md.Add("| Stage | Touch Events | Top10 Share (%) | Top1 Share (%) | HHI | Top 3 Hotspots |")
$md.Add("|---|---:|---:|---:|---:|---|")
foreach ($row in $results) {
    $md.Add("| $($row.Stage) | $($row.M1Touches) | $($row.M1Top10Share) | $($row.M1Top1Share) | $($row.M1HHI) | $($row.M1Top3Files) |")
}

$md.Add("")
$md.Add("## M2 Knowledge Concentration")
$md.Add("")
$md.Add("| Stage | Commits | Authors | Top1 Share (%) | Top2 Share (%) | HHI | Effective Authors | BusFactor50 |")
$md.Add("|---|---:|---:|---:|---:|---:|---:|---:|")
foreach ($row in $results) {
    $md.Add("| $($row.Stage) | $($row.M12Commits) | $($row.M12Authors) | $($row.M2Top1Share) | $($row.M2Top2Share) | $($row.M2HHI) | $($row.M2EffectiveAuthors) | $($row.M2BusFactor50) |")
}

$md.Add("")
$md.Add("## M3 Coordination Latency")
$md.Add("")
$md.Add("| Stage | Handoff Rate (%) | Median Handoff Days | P75 Handoff Days |")
$md.Add("|---|---:|---:|---:|")
foreach ($row in $results) {
    $md.Add("| $($row.Stage) | $($row.M3HandoffRate) | $(Format-Nullable $row.M3MedianHandoffDays) | $(Format-Nullable $row.M3P75HandoffDays) |")
}

$md.Add("")
$md.Add("## M4 Mean Time to Repair")
$md.Add("")
$md.Add("### M4A File-Touch Proxy")
$md.Add("")
$md.Add("| Stage | Samples | MTTR Mean (days) | MTTR Median (days) | P75 (days) |")
$md.Add("|---|---:|---:|---:|---:|")
foreach ($row in $results) {
    $md.Add("| $($row.Stage) | $($row.M4Samples) | $(Format-Nullable $row.M4MTTRMeanDays) | $(Format-Nullable $row.M4MTTRMedianDays) | $(Format-Nullable $row.M4MTTRP75Days) |")
}

$md.Add("")
$md.Add("### M4B Bug-Only Issues")
if ($bugIssuePhaseStats.Count -gt 0) {
    $md.Add("")
    $md.Add("| Stage | Samples | MTTR Mean (days) | MTTR Median (days) | P75 (days) |")
    $md.Add("|---|---:|---:|---:|---:|")
    foreach ($row in $bugIssuePhaseStats) {
        $md.Add("| $($row.Stage) | $($row.Count) | $(Format-Nullable $row.Mean) | $(Format-Nullable $row.Median) | $(Format-Nullable $row.P75) |")
    }
}

Set-Content -Path $OutputMarkdown -Value ($md -join "`r`n") -Encoding UTF8

# Also emit compact console summary for quick check
$results | Select-Object Stage, Anchor, CumRange, SegRange, M12Commits, M12Authors, M1HHI, M2Top1Share, M3MedianHandoffDays, M4MTTRMeanDays | Format-Table -AutoSize
Write-Output "Markdown generated: $OutputMarkdown"
