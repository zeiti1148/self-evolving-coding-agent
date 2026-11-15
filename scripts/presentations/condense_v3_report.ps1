param(
    [string]$SourcePath = 'docs/presentations/coding-agent-v3-stage-report-simple.pptx',
    [string]$SourceContentPath = 'docs/presentations/v3-stage-report-simple-content.json',
    [string]$ContentPath = 'docs/presentations/v3-stage-report-6slides-content.json',
    [string]$OutputPath = 'docs/presentations/coding-agent-v3-stage-report-6slides.pptx',
    [string]$RenderDirectory = '.cache/report-v3/20261102-six'
)

# Edit a private copy using native PowerPoint objects, preserving the template.
# Requires desktop PowerPoint. Original presentations are never opened for editing.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$sourceFile = Join-Path $root $SourcePath
$targetFile = Join-Path $root $OutputPath
$renderRoot = Join-Path $root $RenderDirectory
$sourceData = Get-Content -LiteralPath (Join-Path $root $SourceContentPath) -Raw -Encoding UTF8 | ConvertFrom-Json
$content = Get-Content -LiteralPath (Join-Path $root $ContentPath) -Raw -Encoding UTF8 | ConvertFrom-Json
if (Test-Path -LiteralPath $targetFile) { throw "Output already exists: $targetFile" }
if ($content.slides.Count -ne 6) { throw 'Expected exactly six slides including the cover.' }
$selected = @($content.slides | ForEach-Object { [int]$_.source_slide })
if (($selected | Select-Object -Unique).Count -ne 6) { throw 'Source slide selections must be unique.' }
$sourceHash = (Get-FileHash -LiteralPath $sourceFile -Algorithm SHA256).Hash
New-Item -ItemType Directory -Path (Split-Path $targetFile), $renderRoot -Force | Out-Null
$draftFile = Join-Path $renderRoot ('draft-' + [Guid]::NewGuid().ToString('N') + '.pptx')
Copy-Item -LiteralPath $sourceFile -Destination $draftFile

function Normalize-Text([string]$value) { return ($value -replace "`r`n|`r|`v", "`n").TrimEnd("`n") }
function Add-Replacements($oldValue, $newValue, $pairs, [string]$field) {
    if ($oldValue -is [string]) {
        $pairs.Add([pscustomobject]@{old=(Normalize-Text $oldValue);new=[string]$newValue;field=$field;hits=0})
    } elseif ($oldValue -is [Array]) {
        if ($oldValue.Count -ne $newValue.Count) { throw "Different item count: $field" }
        for ($i=0; $i -lt $oldValue.Count; $i++) { Add-Replacements $oldValue[$i] $newValue[$i] $pairs "$field/$i" }
    } else { throw "Unsupported content: $field" }
}
function Set-ShapeText($shape, [string]$value) {
    $width=$shape.Width; $height=$shape.Height
    $shape.TextFrame.TextRange.Text=$value
    $shape.Width=$width; $shape.Height=$height
}
function Measure-Text($shape, [int]$index) {
    $frame=$shape.TextFrame; $range=$frame.TextRange
    [pscustomobject]@{
        slide=$index; text=$range.Text; left=$shape.Left; top=$shape.Top; width=$shape.Width; height=$shape.Height
        bound_width=$range.BoundWidth; bound_height=$range.BoundHeight
        overflow_height=($range.BoundHeight -gt ($shape.Height-$frame.MarginTop-$frame.MarginBottom+3))
        overflow_width=($range.BoundWidth -gt ($shape.Width-$frame.MarginLeft-$frame.MarginRight+3))
    }
}

$app=$null; $deck=$null
try {
    $app=New-Object -ComObject PowerPoint.Application
    $deck=$app.Presentations.Open($draftFile,0,0,0)
    for ($i=$deck.Slides.Count; $i -ge 1; $i--) {
        if ($i -notin $selected) { $deck.Slides.Item($i).Delete() }
    }
    $geometry=[Collections.Generic.List[object]]::new()
    $tables=0
    for ($i=0; $i -lt $content.slides.Count; $i++) {
        $data=$content.slides[$i]
        $oldData=$sourceData.slides[[int]$data.source_slide-1]
        $slide=$deck.Slides.Item($i+1)
        if ($data.layout -ne $oldData.layout) { throw 'Source layout must be retained.' }
        $pairs=[Collections.Generic.List[object]]::new()
        foreach ($prop in $oldData.PSObject.Properties) {
            if ($prop.Name -notin @('layout','speech','sources','table')) {
                Add-Replacements $prop.Value $data.($prop.Name) $pairs $prop.Name
            }
        }
        foreach ($shape in $slide.Shapes) {
            if ($shape.HasTable -eq -1) {
                $tables++
                $table=$shape.Table
                if ($table.Rows.Count -ne $data.table.Count -or $table.Columns.Count -ne $data.table[0].Count) {
                    throw 'Table dimensions must be retained.'
                }
                for ($row=1; $row -le $table.Rows.Count; $row++) {
                    for ($col=1; $col -le $table.Columns.Count; $col++) {
                        $cell=$table.Cell($row,$col).Shape
                        $cell.TextFrame.TextRange.Text=[string]$data.table[$row-1][$col-1]
                        $geometry.Add((Measure-Text $cell ($i+1)))
                    }
                }
            } elseif ($shape.HasTextFrame -eq -1 -and $shape.TextFrame.HasText -eq -1) {
                if ($shape.Left -gt 880 -and $shape.Top -gt 500) {
                    Set-ShapeText $shape ('{0:D2}' -f ($i+1))
                } else {
                    $oldText=Normalize-Text $shape.TextFrame.TextRange.Text
                    $matches=@($pairs | Where-Object { $_.old -ceq $oldText })
                    if ($matches.Count -gt 1) { throw "Ambiguous text match on slide $($i+1)." }
                    if ($matches.Count -eq 1) {
                        Set-ShapeText $shape $matches[0].new
                        $matches[0].hits++
                    }
                }
                $geometry.Add((Measure-Text $shape ($i+1)))
            }
        }
        $missing=@($pairs | Where-Object { $_.hits -ne 1 })
        if ($missing.Count) { throw "Missing text replacements on slide $($i+1): $($missing.field -join ', ')" }
        $notes=$slide.NotesPage.Shapes.Placeholders.Item(2).TextFrame.TextRange
        $notes.Text=$data.speech+"`r`r资料依据：`r"+($data.sources -join "`r")
        $notes.Font.Name='Microsoft YaHei'; $notes.Font.NameFarEast='Microsoft YaHei'; $notes.Font.Size=12
    }
    $geometry | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $renderRoot 'text-geometry.json') -Encoding UTF8
    $overflows=@($geometry | Where-Object { $_.overflow_height -or $_.overflow_width })
    if ($overflows.Count) {
        $overflows | ConvertTo-Json -Depth 6 | Write-Output
        throw "Text overflow detected: $($overflows.Count) objects"
    }
    $deck.Save()
    foreach ($slide in $deck.Slides) {
        $slide.Export((Join-Path $renderRoot ('slide-{0:00}.png' -f $slide.SlideIndex)),'PNG',1600,900)
    }
    $deck.SaveAs($targetFile,24)
    if ((Get-FileHash -LiteralPath $sourceFile -Algorithm SHA256).Hash -ne $sourceHash) { throw 'Source presentation changed.' }
    $receipt=[pscustomobject]@{
        output=$targetFile; slides=$deck.Slides.Count; editable_tables=$tables; notes=$deck.Slides.Count
        source=$sourceFile; source_sha256=$sourceHash; reused_source_slides=$selected
        width=$deck.PageSetup.SlideWidth; height=$deck.PageSetup.SlideHeight
        render_directory=$renderRoot; text_overflows=$overflows.Count
    } | ConvertTo-Json -Depth 4
    $receipt | Set-Content -LiteralPath (Join-Path $renderRoot 'authoring-receipt.json') -Encoding UTF8
    Write-Output $receipt
} finally {
    if ($deck) {
        $deck.Saved=-1
        $deck.Close()
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($deck)
    }
    if ($app) {
        if ($app.Presentations.Count -eq 0) { $app.Quit() }
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($app)
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
