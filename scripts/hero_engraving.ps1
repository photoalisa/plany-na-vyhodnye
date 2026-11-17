# Гравюры для первого экрана корневой страницы (yandex-root).
# Исходники — фотографии новосибирских объектов из src/hero-refs.
# Скрипт: осветление по dodge (gray * 255 / (256 - blur(G_инверсия))),
# затем три тона (бумага #FBFAF7, серый, тёмно-серые чернила) и запись JPEG.
#
# Запуск:  powershell -ExecutionPolicy Bypass -File scripts/hero_engraving.ps1
# Результат: yandex-root/assets/hero-a.jpg … hero-d.jpg

Add-Type -AssemblyName System.Drawing

function New-Engraving {
  param(
    [string]$Src, [string]$Dst, [int]$Width, [int]$Height,
    [int]$CropX, [int]$CropY, [int]$CropW, [int]$CropH,
    [double]$BlurRadius = 70, [int]$Steps = 3,
    [double]$Lo = 0.50, [double]$Hi = 0.95
  )

  $img = [System.Drawing.Image]::FromFile($Src)
  $bmp = New-Object System.Drawing.Bitmap $Width, $Height
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
  $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
  $dstRect = New-Object System.Drawing.Rectangle 0, 0, $Width, $Height
  $srcRect = New-Object System.Drawing.Rectangle $CropX, $CropY, $CropW, $CropH
  $g.DrawImage($img, $dstRect, $srcRect, [System.Drawing.GraphicsUnit]::Pixel)
  $g.Dispose(); $img.Dispose()

  $rect = New-Object System.Drawing.Rectangle 0, 0, $Width, $Height
  $data = $bmp.LockBits($rect, [System.Drawing.Imaging.ImageLockMode]::ReadWrite, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
  $bytes = $Width * $Height * 4
  $buf = New-Object byte[] $bytes
  [System.Runtime.InteropServices.Marshal]::Copy($data.Scan0, $buf, 0, $bytes)

  $n = $Width * $Height
  $gray = New-Object double[] $n
  $inv = New-Object double[] $n
  for ($i = 0; $i -lt $n; $i++) {
    $o = $i * 4
    $gr = 0.3 * $buf[$o + 2] + 0.59 * $buf[$o + 1] + 0.11 * $buf[$o]
    $gray[$i] = $gr
    $inv[$i] = 255.0 - $gr
  }

  $iw = $Width + 1
  $sum = New-Object 'long[]' ($iw * ($Height + 1))
  for ($y = 0; $y -lt $Height; $y++) {
    $rowAcc = 0L
    $base = ($y + 1) * $iw
    $prev = $y * $iw
    for ($x = 0; $x -lt $Width; $x++) {
      $rowAcc += [long]$inv[$y * $Width + $x]
      $sum[$base + $x + 1] = $sum[$prev + $x + 1] + $rowAcc
    }
  }

  $inkR = 45.0; $inkG = 42.0; $inkB = 38.0
  $papR = 251.0; $papG = 250.0; $papB = 247.0
  $den = [double]($Steps - 1)

  $r = [int]$BlurRadius
  for ($y = 0; $y -lt $Height; $y++) {
    $y0 = $y - $r; if ($y0 -lt 0) { $y0 = 0 }
    $y1 = $y + $r; if ($y1 -ge $Height) { $y1 = $Height - 1 }
    for ($x = 0; $x -lt $Width; $x++) {
      $x0 = $x - $r; if ($x0 -lt 0) { $x0 = 0 }
      $x1 = $x + $r; if ($x1 -ge $Width) { $x1 = $Width - 1 }
      $a = $sum[$y0 * $iw + $x0]
      $b = $sum[$y0 * $iw + $x1 + 1]
      $c = $sum[($y1 + 1) * $iw + $x0]
      $d = $sum[($y1 + 1) * $iw + $x1 + 1]
      $area = ($x1 - $x0 + 1) * ($y1 - $y0 + 1)
      $blur = ($d - $b - $c + $a) / $area
      $idx = $y * $Width + $x
      $v = $gray[$idx] * 255.0 / (256.0 - $blur)
      if ($v -lt 0) { $v = 0 }
      if ($v -gt 250) { $v = 250 }
      $t = ($v / 250.0 - $Lo) / ($Hi - $Lo)
      if ($t -lt 0) { $t = 0 }
      if ($t -gt 1) { $t = 1 }
      $q = [math]::Round($t * $den) / $den
      $o = $idx * 4
      $buf[$o]     = [byte][math]::Round($inkB + ($papB - $inkB) * $q)
      $buf[$o + 1] = [byte][math]::Round($inkG + ($papG - $inkG) * $q)
      $buf[$o + 2] = [byte][math]::Round($inkR + ($papR - $inkR) * $q)
    }
  }

  [System.Runtime.InteropServices.Marshal]::Copy($buf, 0, $data.Scan0, $bytes)
  $bmp.UnlockBits($data)

  $codec = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object { $_.MimeType -eq 'image/jpeg' }
  $ep = [System.Drawing.Imaging.EncoderParameter]::new([System.Drawing.Imaging.Encoder]::Quality, [long]92)
  $params = New-Object System.Drawing.Imaging.EncoderParameters 1
  $params.Param[0] = $ep
  $bmp.Save($Dst, $codec, $params)
  $bmp.Dispose()
  Write-Output ("{0} -> {1} {2}x{3} {4} б" -f (Split-Path $Src -Leaf), (Split-Path $Dst -Leaf), $Width, $Height, (Get-Item -LiteralPath $Dst).Length)
}

$root = Split-Path -Parent $PSScriptRoot
$refs = Join-Path $root 'src\hero-refs'
$out = Join-Path $root 'yandex-root\assets'

New-Engraving -Src "$refs\opera.jpg"      -Dst "$out\hero-a.jpg" -Width 620 -Height 349 -CropX 900  -CropY 240 -CropW 2600 -CropH 1462 -BlurRadius 80
New-Engraving -Src "$refs\museum.jpg"     -Dst "$out\hero-b.jpg" -Width 440 -Height 330 -CropX 1700 -CropY 60  -CropW 1200 -CropH 900  -BlurRadius 58
New-Engraving -Src "$refs\bridge.jpg"     -Dst "$out\hero-c.jpg" -Width 560 -Height 315 -CropX 0    -CropY 90  -CropW 1920 -CropH 1080 -BlurRadius 70
New-Engraving -Src "$refs\technopark.jpg" -Dst "$out\hero-d.jpg" -Width 536 -Height 402 -CropX 392  -CropY 131 -CropW 2314 -CropH 1736 -BlurRadius 70
