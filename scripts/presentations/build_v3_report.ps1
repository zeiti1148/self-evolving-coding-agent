param(
    [string]$ContentPath = 'docs/presentations/v3-stage-report-content.json',
    [string]$OutputPath = 'docs/presentations/coding-agent-v3-stage-report.pptx',
    [string]$RenderDirectory = '.cache/report-v3/20261101/slides'
)

# Native PowerPoint authoring. Requires desktop PowerPoint on Windows.
# Opens only a new presentation without a window; never edits existing decks.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$contentFile = Join-Path $root $ContentPath
$targetFile = Join-Path $root $OutputPath
$renderRoot = Join-Path $root $RenderDirectory
if (Test-Path -LiteralPath $targetFile) { throw "Output already exists: $targetFile" }
$content = Get-Content -LiteralPath $contentFile -Raw -Encoding UTF8 | ConvertFrom-Json
New-Item -ItemType Directory -Path (Split-Path $targetFile), $renderRoot -Force | Out-Null

function Color([string]$hex) {
    $value = $hex.TrimStart('#')
    return [Convert]::ToInt32($value.Substring(0,2),16) +
        256 * [Convert]::ToInt32($value.Substring(2,2),16) +
        65536 * [Convert]::ToInt32($value.Substring(4,2),16)
}
$navy = Color '17243D'
$blue = Color '2C72DB'
$muted = Color '7E8BA0'
$light = Color 'EDF4FF'
$white = Color 'FFFFFF'
$bodyColor = Color '384D69'
$rule = Color 'D7E3F4'
$script:shapes = [Collections.Generic.List[object]]::new()

function Add-Text($slide, [string]$text, [double]$x, [double]$y, [double]$w, [double]$h, [double]$size=20, [int]$color=$bodyColor, [bool]$bold=$false, [string]$font='宋体') {
    $shape = $slide.Shapes.AddTextbox(1,$x,$y,$w,$h)
    $shape.Line.Visible=0
    $shape.Fill.Visible=0
    $shape.TextFrame.MarginLeft=0
    $shape.TextFrame.MarginRight=0
    $shape.TextFrame.MarginTop=0
    $shape.TextFrame.MarginBottom=0
    $shape.TextFrame.WordWrap=-1
    $shape.TextFrame.AutoSize=0
    $range=$shape.TextFrame.TextRange
    $range.Text=$text
    $range.Font.Name=$font
    $range.Font.NameFarEast=$font
    $range.Font.Size=$size
    $range.Font.Color.RGB=$color
    $range.Font.Bold= $(if($bold){-1}else{0})
    $range.ParagraphFormat.SpaceBefore=0
    $range.ParagraphFormat.SpaceAfter=7
    $range.ParagraphFormat.LineRuleWithin=-1
    $range.ParagraphFormat.SpaceWithin=1.12
    # Turning off Office's initial automatic sizing can shrink an empty text
    # box. Restore the requested frame only after assigning its actual text.
    $shape.Width=$w
    $shape.Height=$h
    $script:shapes.Add([pscustomobject]@{slide=$slide.SlideIndex;shape=$shape;label=$text})
    return $shape
}
function Add-Panel($slide,[double]$x,[double]$y,[double]$w,[double]$h,[string]$title,[string]$body,[double]$bodySize=18) {
    $panel=$slide.Shapes.AddShape(5,$x,$y,$w,$h)
    $panel.Fill.ForeColor.RGB=$light
    $panel.Line.Visible=0
    $panel.Adjustments.Item(1)=0.13
    [void](Add-Text $slide $title ($x+22) ($y+19) ($w-44) 30 21 $blue $false)
    [void](Add-Text $slide $body ($x+22) ($y+59) ($w-44) ($h-69) $bodySize $bodyColor $false)
}
function Add-Header($slide,$data,[int]$index) {
    $slide.FollowMasterBackground=0
    $slide.Background.Fill.ForeColor.RGB=$white
    $bar=$slide.Shapes.AddShape(1,0,0,960,8)
    $bar.Fill.ForeColor.RGB=$blue
    $bar.Line.Visible=0
    [void](Add-Text $slide $data.title 56 36 848 50 29 $navy $true)
    [void](Add-Text $slide $data.subtitle 56 96 848 26 12.5 $muted $false 'Microsoft YaHei')
    [void](Add-Text $slide ('{0:D2}' -f $index) 886 511 30 18 10.5 $muted $false 'Arial')
}
function Add-Footnote($slide,[string]$text) {
    [void](Add-Text $slide $text 56 479 826 28 11 $muted $false 'Microsoft YaHei')
}
function Add-Table($slide,$rows,[double]$x,[double]$y,[double]$w,[double]$h,$widths,[double]$size=18) {
    $tableShape=$slide.Shapes.AddTable($rows.Count,$rows[0].Count,$x,$y,$w,$h)
    $table=$tableShape.Table
    for($col=1;$col -le $widths.Count;$col++) { $table.Columns.Item($col).Width=$widths[$col-1] }
    for($row=1;$row -le $rows.Count;$row++) {
        for($col=1;$col -le $rows[0].Count;$col++) {
            $cell=$table.Cell($row,$col)
            $cell.Shape.Fill.ForeColor.RGB=$(if($row -eq 1){$light}else{$white})
            foreach($border in 1..4){$cell.Borders.Item($border).ForeColor.RGB=$rule; $cell.Borders.Item($border).Weight=0.7}
            $frame=$cell.Shape.TextFrame
            $frame.MarginLeft=14; $frame.MarginRight=10; $frame.MarginTop=7; $frame.MarginBottom=6
            $frame.VerticalAnchor=3
            $range=$frame.TextRange
            $range.Text=[string]$rows[$row-1][$col-1]
            $range.Font.Name='Microsoft YaHei';$range.Font.NameFarEast='Microsoft YaHei'
            $range.Font.Size=$size
            $range.Font.Bold=$(if($row -eq 1){-1}else{0})
            $range.Font.Color.RGB=$(if($row -eq 1){$blue}else{$bodyColor})
            $range.ParagraphFormat.SpaceAfter=0
            $script:shapes.Add([pscustomobject]@{slide=$slide.SlideIndex;shape=$cell.Shape;label=$range.Text})
        }
    }
}

$app=$null
$deck=$null
try {
    $app=New-Object -ComObject PowerPoint.Application
    $deck=$app.Presentations.Add(0)
    $deck.PageSetup.SlideWidth=960
    $deck.PageSetup.SlideHeight=540
    # Office's late-bound BuiltInDocumentProperties setter is not reliable in
    # PowerShell. The presentation title and sources are stored in slides/notes.
    for($index=0;$index -lt $content.slides.Count;$index++) {
        $data=$content.slides[$index]
        $slide=$deck.Slides.Add($index+1,12)
        if($data.layout -eq 'cover') {
            $slide.FollowMasterBackground=0; $slide.Background.Fill.ForeColor.RGB=$white
            $bar=$slide.Shapes.AddShape(1,0,0,960,8); $bar.Fill.ForeColor.RGB=$blue; $bar.Line.Visible=0
            [void](Add-Text $slide $data.title 56 35 848 102 32 $navy $true)
            [void](Add-Text $slide $data.subtitle 58 151 840 24 13 $muted $false 'Microsoft YaHei')
            [void](Add-Text $slide $data.statement 62 219 832 94 24 $navy $false)
            for($j=0;$j -lt 3;$j++){Add-Panel $slide (56+288*$j) 360 270 119 $data.cards[$j][0] $data.cards[$j][1] 17}
            [void](Add-Text $slide '01' 886 511 30 18 10.5 $muted $false 'Arial')
        } else {
            Add-Header $slide $data ($index+1)
            switch($data.layout) {
                'milestones' {
                    for($j=0;$j -lt 6;$j++){Add-Panel $slide (56+288*($j%3)) (149+153*[Math]::Floor($j/3)) 270 132 $data.cards[$j][0] $data.cards[$j][1] 17}
                }
                'experiences' {
                    for($j=0;$j -lt 3;$j++){Add-Panel $slide (56+288*$j) 151 270 142 $data.cards[$j][0] $data.cards[$j][1] 17.5}
                    for($j=0;$j -lt 3;$j++){
                        [void](Add-Text $slide ('{0:00}' -f ($j+1)) 62 (322+45*$j) 35 27 17 $blue $true 'Arial')
                        [void](Add-Text $slide $data.lines[$j] 108 (320+45*$j) 790 36 18.5 $bodyColor $false)
                    }
                }
                'isolation' {
                    for($j=0;$j -lt 2;$j++){Add-Panel $slide (56+434*$j) 151 414 249 $data.panels[$j][0] $data.panels[$j][1] 18}
                    [void](Add-Text $slide $data.bottom 62 428 837 35 18 $blue $false)
                }
                'transfer' {
                    Add-Table $slide $data.table 56 144 848 249 @(238,305,305) 18
                    [void](Add-Text $slide $data.leftResult 65 414 365 59 17 $bodyColor $false)
                    [void](Add-Text $slide $data.rightResult 454 414 450 59 17 $blue $false)
                }
                'roles' {
                    for($j=0;$j -lt 3;$j++){Add-Panel $slide (56+288*$j) 151 270 144 $data.cards[$j][0] $data.cards[$j][1] 17}
                    for($j=0;$j -lt 3;$j++){
                        [void](Add-Text $slide ('{0:00}' -f ($j+1)) 62 (323+45*$j) 35 27 17 $blue $true 'Arial')
                        [void](Add-Text $slide $data.steps[$j] 108 (321+45*$j) 790 36 18.5 $bodyColor $false)
                    }
                }
                'screen' {
                    $imagePath=Join-Path (Split-Path $contentFile) $data.image
                    [void]$slide.Shapes.AddPicture($imagePath,0,-1,56,144,590,331.875)
                    for($j=0;$j -lt 3;$j++){
                        [void](Add-Text $slide $data.highlights[$j][0] 674 (154+108*$j) 230 30 21 $blue $false)
                        [void](Add-Text $slide $data.highlights[$j][1] 674 (193+108*$j) 230 63 16.5 $bodyColor $false)
                    }
                }
                'evidence' {
                    Add-Table $slide $data.table 56 144 848 270 @(285,285,278) 17
                    [void](Add-Text $slide $data.limitation 58 436 844 32 15.5 $blue $true 'Microsoft YaHei')
                }
                'next' {
                    for($j=0;$j -lt 2;$j++){Add-Panel $slide (56+434*$j) 149 414 139 $data.priorities[$j][0] $data.priorities[$j][1] 17.5}
                    for($j=0;$j -lt 3;$j++){Add-Panel $slide (56+288*$j) 322 270 132 $data.cards[$j][0] $data.cards[$j][1] 17}
                }
                default {throw "Unknown slide layout: $($data.layout)"}
            }
            Add-Footnote $slide $data.footnote
        }
        $notes=$slide.NotesPage.Shapes.Placeholders.Item(2).TextFrame.TextRange
        $notes.Text=$data.speech+"`r`r资料依据：`r"+($data.sources -join "`r")
        $notes.Font.Name='Microsoft YaHei'
        $notes.Font.NameFarEast='Microsoft YaHei'
        $notes.Font.Size=12
    }
    $geometry=@()
    foreach($entry in $script:shapes){
        $shape=$entry.shape
        $frame=$shape.TextFrame
        $range=$frame.TextRange
        $geometry+=[pscustomobject]@{
            slide=$entry.slide;text=$entry.label;left=$shape.Left;top=$shape.Top;width=$shape.Width;height=$shape.Height
            bound_width=$range.BoundWidth;bound_height=$range.BoundHeight
            overflow_height=($range.BoundHeight -gt ($shape.Height-$frame.MarginTop-$frame.MarginBottom+3))
            overflow_width=($range.BoundWidth -gt ($shape.Width-$frame.MarginLeft-$frame.MarginRight+3))
        }
    }
    $geometry | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $renderRoot 'text-geometry.json') -Encoding UTF8
    $overflows=@($geometry | Where-Object {$_.overflow_height -or $_.overflow_width})
    if($overflows.Count -gt 0) {
        $overflows | ConvertTo-Json -Depth 6 | Write-Output
        throw "Text overflow detected: $($overflows.Count) objects"
    }
    $deck.SaveAs($targetFile,24)
    foreach($slide in $deck.Slides){$slide.Export((Join-Path $renderRoot ('slide-{0:00}.png' -f $slide.SlideIndex)),'PNG',1600,900)}
    [pscustomobject]@{output=$targetFile;slides=$deck.Slides.Count;render_directory=$renderRoot;editable_tables=2;notes=$deck.Slides.Count}|ConvertTo-Json
} finally {
    if($deck){$deck.Close();[void][Runtime.InteropServices.Marshal]::ReleaseComObject($deck)}
    if($app){
        if($app.Presentations.Count -eq 0){$app.Quit()}
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($app)
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
