#!/usr/bin/env node
// Проверки чистой логики виджета (package/contents/code/logic.js).
// Запуск:  node tests/logic.test.js
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const sourcePath = path.join(__dirname, "..", "package", "contents", "code", "logic.js");
const source = fs.readFileSync(sourcePath, "utf8").replace(/^\s*\.pragma library\s*$/m, "");
const logic = vm.createContext({});
vm.runInContext(source, logic, { filename: sourcePath });

const now = Date.parse("2026-01-01T12:00:00Z");
const inHours = (hours) => new Date(now + hours * 3600 * 1000).toISOString();

// --- пороги цвета -----------------------------------------------------------
assert.strictEqual(logic.colorTier(-1), "unknown");
assert.strictEqual(logic.colorTier(0), "ok");
assert.strictEqual(logic.colorTier(59.9), "ok");
assert.strictEqual(logic.colorTier(60), "warn");
assert.strictEqual(logic.colorTier(84.9), "warn");
assert.strictEqual(logic.colorTier(85), "crit");
assert.strictEqual(logic.colorTier(100), "crit");
assert.strictEqual(logic.colorTier(NaN), "unknown");

// --- проценты ---------------------------------------------------------------
assert.strictEqual(logic.percentText(-1), "—");
assert.strictEqual(logic.percentText(0), "0%");
assert.strictEqual(logic.percentText(12.6), "13%");
assert.strictEqual(logic.percentText(99.4), "99%");

// --- обратный отсчёт --------------------------------------------------------
assert.strictEqual(logic.formatReset("", now), "");
assert.strictEqual(logic.formatReset("не дата", now), "");
assert.strictEqual(logic.formatReset(inHours(-1), now), "сброс сейчас");

const inMinutes = logic.formatReset(new Date(now + 40 * 60 * 1000).toISOString(), now);
assert.match(inMinutes, /^сброс через 40 мин · \d\d:\d\d$/);

const inHoursText = logic.formatReset(inHours(5), now);
assert.match(inHoursText, /^сброс через 5 ч 0 мин · \d\d:\d\d$/);

const inDaysText = logic.formatReset(inHours(49), now);
assert.match(inDaysText, /^сброс через 2 д 1 ч · \d\d:\d\d$/);

assert.match(logic.formatResetFull(inHours(5), now), /^точное время сброса: \d\d\.\d\d\.\d{4} \d\d:\d\d:\d\d$/);

// --- строка под полосой -----------------------------------------------------
assert.strictEqual(logic.resetText(null, now), "");
assert.strictEqual(logic.resetText({ status: "ok", resetsAt: inHours(2) }, now).indexOf("лимит исчерпан"), -1);
assert.strictEqual(logic.resetText({ status: "active", resetsAt: inHours(2) }, now).indexOf("лимит исчерпан"), -1);
assert.strictEqual(
    logic.resetText({ status: "exceeded", resetsAt: inHours(2) }, now).startsWith("лимит исчерпан · сброс через"),
    true
);

// --- строка состояния -------------------------------------------------------
assert.strictEqual(logic.statusText("", ""), "загрузка...");
assert.strictEqual(logic.statusText("", "12:34:56"), "обновлено 12:34:56");
assert.strictEqual(logic.statusText("ключ отклонён (401) — проверьте auth.json", "12:34:56"), "ошибка: ключ отклонён (401) — проверьте auth.json");
assert.strictEqual(logic.statusText("x".repeat(100), "12:34:56").length, 63);

// --- текст уведомления ------------------------------------------------------
assert.match(
    logic.notifyBody({ percent: 91.2, resetsAt: inHours(3) }, 85, now),
    /^Использовано 91% \(порог 85%\)\. сброс через 3 ч 0 мин · \d\d:\d\d\. Отключить можно в настройках\.$/
);

// --- разбор ответа backend --------------------------------------------------
const payload = '{"ok":true,"time":"12:00:00","usage":{"rolling":{"percent":1,"status":"ok","resetsAt":""}}}';
assert.strictEqual(JSON.stringify(logic.parseBackendJson(payload + "\n")), JSON.stringify(JSON.parse(payload)));
assert.strictEqual(logic.parseBackendJson(""), null);
assert.strictEqual(logic.parseBackendJson("не json"), null);

assert.strictEqual(logic.stdoutText({ stdout: payload }), payload);
assert.strictEqual(logic.stdoutText({ "exit code": 0, stderr: [] }), "");
assert.strictEqual(logic.stdoutText(null), "");

console.log("logic.js: все проверки пройдены");
