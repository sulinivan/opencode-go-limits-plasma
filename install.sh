#!/usr/bin/env bash
# Устанавливает виджет в домашний каталог пользователя. Root не нужен.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
package="$here/package"
notify_dir="$HOME/.local/share/knotifications6"

if ! kpackagetool6 --type Plasma/Applet --install "$package" 2>/dev/null; then
    kpackagetool6 --type Plasma/Applet --upgrade "$package"
fi

mkdir -p "$notify_dir"
cp "$package/contents/notifications/opencode-limits.notifyrc" "$notify_dir/"

echo "Готово."
echo "Добавьте виджет: правый клик по панели или рабочему столу → «Добавить виджеты» → OpenCode Go Limits"
