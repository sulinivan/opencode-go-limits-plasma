.pragma library

// Чистая логика виджета: пороги цвета, обратный отсчёт, тексты, разбор
// ответа backend.py. Здесь нет ни сети, ни QML — поэтому файл легко
// проверяется обычным Node.js (см. tests/logic.test.js).

var WARN_PERCENT = 60
var CRIT_PERCENT = 85

var TITLE = "OpenCode Go"

var WINDOW_TITLES = {
    "rolling": "5 часов",
    "weekly": "неделя",
    "monthly": "месяц"
}

// "ok" | "warn" | "crit" | "unknown"
function colorTier(percent) {
    if (percent === undefined || percent === null || isNaN(percent) || percent < 0)
        return "unknown"
    if (percent >= CRIT_PERCENT)
        return "crit"
    if (percent >= WARN_PERCENT)
        return "warn"
    return "ok"
}

function percentText(percent) {
    if (percent === undefined || percent === null || isNaN(percent) || percent < 0)
        return "—"
    return Math.round(percent) + "%"
}

function pad2(value) {
    return value < 10 ? "0" + value : String(value)
}

// Дата сброса в локальном времени: "HH:mm" и "dd.MM.yyyy HH:mm:ss".
function resetMoment(iso, nowMs) {
    if (!iso)
        return null
    var time = Date.parse(iso)
    if (isNaN(time))
        return null

    var remaining = time - nowMs
    var moment = new Date(time)

    var days = Math.floor(remaining / 86400000)
    var hours = Math.floor(remaining / 3600000) % 24
    var totalHours = Math.floor(remaining / 3600000)
    var minutes = Math.floor(remaining / 60000) % 60
    var relative
    if (remaining <= 0)
        relative = "сброс сейчас"
    else if (days >= 1)
        relative = days + " д " + hours + " ч"
    else if (totalHours >= 1)
        relative = totalHours + " ч " + minutes + " мин"
    else
        relative = Math.max(1, Math.floor(remaining / 60000)) + " мин"

    return {
        relative: relative,
        clock: pad2(moment.getHours()) + ":" + pad2(moment.getMinutes()),
        full: pad2(moment.getDate()) + "." + pad2(moment.getMonth() + 1) + "." + moment.getFullYear()
            + " " + pad2(moment.getHours()) + ":" + pad2(moment.getMinutes()) + ":" + pad2(moment.getSeconds())
    }
}

// "сброс через 2 ч 5 мин · 14:30" — как в оригинальном виджете.
function formatReset(iso, nowMs) {
    var moment = resetMoment(iso, nowMs)
    if (!moment)
        return ""
    if (moment.relative === "сброс сейчас")
        return moment.relative
    return "сброс через " + moment.relative + " · " + moment.clock
}

function formatResetFull(iso, nowMs) {
    var moment = resetMoment(iso, nowMs)
    return moment ? "точное время сброса: " + moment.full : ""
}

// Текст под полосой: строка сброса плюс пометка об исчерпанном лимите.
function resetText(item, nowMs) {
    if (!item)
        return ""
    var text = formatReset(item.resetsAt, nowMs)
    var status = item.status || ""
    if (status && status !== "ok" && status !== "active")
        text = "лимит исчерпан · " + text
    return text
}

// Строка состояния внизу виджета.
function statusText(error, updatedAt) {
    if (error) {
        var text = "ошибка: " + error
        return text.length > 62 ? text.substring(0, 60) + "..." : text
    }
    if (!updatedAt)
        return "загрузка..."
    return "обновлено " + updatedAt
}

function notifyBody(item, threshold, nowMs) {
    return "Использовано " + Math.round(item.percent) + "% (порог " + threshold + "%). "
        + formatReset(item.resetsAt, nowMs) + ". Отключить можно в настройках."
}

// Движок "executable" отдаёт вывод процесса в поле stdout.
function stdoutText(data) {
    return (data && typeof data["stdout"] === "string") ? data["stdout"] : ""
}

function parseBackendJson(text) {
    if (!text)
        return null
    try {
        return JSON.parse(String(text).trim())
    } catch (error) {
        return null
    }
}
