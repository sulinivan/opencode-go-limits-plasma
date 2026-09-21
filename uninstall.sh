#!/usr/bin/env bash
# Удаляет виджет из домашнего каталога пользователя.
set -uo pipefail

kpackagetool6 --type Plasma/Applet --remove org.opencode.limits || true
rm -f "$HOME/.local/share/knotifications6/opencode-limits.notifyrc"

echo "Виджет удалён. Если он уже добавлен на панель, уберите его через контекстное меню."
