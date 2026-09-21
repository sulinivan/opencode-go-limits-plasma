#!/usr/bin/env bash
# Показывает, сколько ресурсов виджет добавляет к plasmashell.
#
# Запустите до добавления виджета, затем после — и сравните RSS/CPU.
#
#   bench/measure.sh [секунды_замера]     (по умолчанию 30)
set -euo pipefail

seconds="${1:-30}"
pid="$(pgrep -x plasmashell | head -n1)"

if [[ -z "$pid" ]]; then
    echo "plasmashell не запущен" >&2
    exit 1
fi

read_cpu() { awk '{print $14 + $15}' "/proc/$pid/stat"; }
read_rss() { awk '/VmRSS/{print $2}' "/proc/$pid/status"; }

ticks_per_second="$(getconf CLK_TCK)"
cpu_before="$(read_cpu)"

sleep "$seconds"

cpu_after="$(read_cpu)"
used_seconds="$(awk -v a="$cpu_before" -v b="$cpu_after" -v hz="$ticks_per_second" 'BEGIN{printf "%.3f", (b-a)/hz}')"
used_percent="$(awk -v u="$used_seconds" -v s="$seconds" 'BEGIN{printf "%.2f", 100*u/s}')"

echo "plasmashell:    pid $pid"
echo "RSS:            $(read_rss) kB"
echo "CPU за ${seconds} с: ${used_seconds} с (${used_percent}%)"
