$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$months=@('января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря')
$names=@('воскресенье','понедельник','вторник','среда','четверг','пятница','суббота')
$now=Get-Date
$dow=$names[[int]$now.DayOfWeek]
Write-Output "Сегодня $($now.Day) $($months[$now.Month-1]) $($now.Year) года, $dow."
