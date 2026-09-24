$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$feed = if ($env:JARVIS_NEWS_FEED_URL) { $env:JARVIS_NEWS_FEED_URL } else { "https://lenta.ru/rss/last24" }
$count = if ($env:JARVIS_NEWS_COUNT) { [int]$env:JARVIS_NEWS_COUNT } else { 5 }
try {
  [xml]$xml = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 10 $feed).Content
  $titles = @()
  if ($xml.rss) { $titles = @($xml.rss.channel.item | Select-Object -First $count | ForEach-Object { $_.title }) }
  elseif ($xml.feed) { $titles = @($xml.feed.entry | Select-Object -First $count | ForEach-Object { $_.title }) }
  $titles = $titles | Where-Object { $_ } | ForEach-Object { ($_ -replace '\.$','') + '.' }
  if (-not $titles) { Write-Output "Источник новостей ответил, но заголовков не нашлось."; exit 0 }
  $titles | ForEach-Object { Write-Output $_ }
} catch {
  Write-Output "Не смог получить новости — сеть или источник недоступны."
}
