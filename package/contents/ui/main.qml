import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.components as PlasmaComponents
import org.kde.plasma.plasma5support as P5Support
import org.kde.kirigami as Kirigami
import org.kde.notification
import "../code/logic.js" as Logic

// Виджет лимитов OpenCode Go.
//
// Раз в `refreshMinutes` минут запускается backend.py: он читает ключ из
// auth.json, опрашивает сервис и кладёт ответ одной строкой JSON в общий
// журнал usage.log. Каждый экземпляр виджета (панель, рабочий стол) раз в
// POL_INTERVAL_MS читает последнюю строку журнала, поэтому обновление,
// нажатое в одном месте, видят все, а отсчёты до сброса не расходятся.
PlasmoidItem {
    id: root

    // --- данные ------------------------------------------------------------

    property var usage: null
    property string lastError: ""
    property string updatedAt: ""
    property bool loading: false
    property string lastPayload: ""
    property string activeSource: ""

    // Процент 5-часового лимита — его показывает компактная версия на панели.
    readonly property real rollingPercent: (root.usage && root.usage.rolling
        && typeof root.usage.rolling.percent === "number") ? root.usage.rolling.percent : -1

    readonly property string backendScript: root.localPath(Qt.resolvedUrl("../code/backend.py"))
    readonly property string stateDir: root.backendScript.substring(0, root.backendScript.lastIndexOf("/") + 1)
    readonly property string usageLog: root.stateDir + "usage.log"

    readonly property string fetchCommand: "/usr/bin/env python3 '" + root.backendScript + "'"
        + (Plasmoid.configuration.notify ? " --threshold " + Plasmoid.configuration.notifyThreshold : "")
    readonly property string pollCommand: "tail -n 1 '" + root.usageLog + "'"

    function localPath(url) {
        var text = String(url)
        return text.indexOf("file://") === 0 ? decodeURIComponent(text.substring(7)) : text
    }

    function tierColor(tier) {
        if (tier === "crit")
            return Kirigami.Theme.negativeTextColor
        if (tier === "warn")
            return Kirigami.Theme.neutralTextColor
        if (tier === "ok")
            return Kirigami.Theme.positiveTextColor
        return Kirigami.Theme.disabledTextColor
    }

    // --- обновление --------------------------------------------------------

    function refresh() {
        if (root.loading)
            return
        root.loading = true
        root.activeSource = root.fetchCommand
        watchdog.restart()
        runner.connectSource(root.activeSource)
    }

    // Читает последнюю строку журнала: так приходят обновления от других
    // экземпляров виджета.
    function poll() {
        poller.disconnectSource(root.pollCommand)
        poller.connectSource(root.pollCommand)
    }

    // Единая точка отрисовки: и свой ответ, и чужая строка журнала.
    // fromPoll = true означает "данные пришли не от нашего запроса" —
    // уведомление в этом случае не отправляем.
    function render(text, fromPoll) {
        if (!text || text === root.lastPayload)
            return false
        var document = Logic.parseBackendJson(text)
        if (!document)
            return false

        root.lastPayload = text
        root.updatedAt = document.time || root.updatedAt
        if (document.ok) {
            root.usage = document.usage || null
            root.lastError = ""
            if (!fromPoll && document.notify === true)
                root.sendNotification()
        } else {
            root.lastError = document.error || "неизвестная ошибка"
        }
        return true
    }

    function sendNotification() {
        notifier.title = "OpenCode Go — 5-часовой лимит"
        notifier.text = Logic.notifyBody(root.usage.rolling, Plasmoid.configuration.notifyThreshold, Date.now())
        notifier.iconName = Plasmoid.icon || "speedometer"
        notifier.sendEvent()
    }

    Notification {
        id: notifier

        componentName: "opencode-limits"
        eventId: "limitReached"
    }

    P5Support.DataSource {
        id: runner

        engine: "executable"
        connectedSources: []

        onNewData: (sourceName, data) => {
            if (sourceName !== root.activeSource)
                return
            runner.disconnectSource(sourceName)
            watchdog.stop()
            root.loading = false
            if (!root.render(Logic.stdoutText(data), false))
                root.lastError = "backend.py завершился без ответа"
        }
    }

    P5Support.DataSource {
        id: poller

        engine: "executable"
        connectedSources: []

        onNewData: (sourceName, data) => {
            poller.disconnectSource(sourceName)
            root.render(Logic.stdoutText(data), true)
        }
    }

    Timer {
        interval: Math.max(1, Plasmoid.configuration.refreshMinutes) * 60000
        running: true
        repeat: true
        onTriggered: root.refresh()
    }

    // Как часто проверять журнал на обновления от других экземпляров.
    readonly property int pollIntervalMs: 3000

    Timer {
        interval: root.pollIntervalMs
        running: true
        repeat: true
        onTriggered: root.poll()
    }

    Timer {
        id: watchdog

        interval: 30000
        onTriggered: {
            if (!root.loading)
                return
            root.loading = false
            root.lastError = "backend.py не ответил вовремя"
        }
    }

    Component.onCompleted: root.refresh()

    // --- поведение ---------------------------------------------------------

    // На рабочем столе показываем полную версию, на панели — компактную.
    preferredRepresentation: Plasmoid.formFactor === PlasmaCore.Types.Planar ? fullRepresentation : null

    compactRepresentation: MouseArea {
        id: compact

        implicitWidth: compactRow.implicitWidth + Kirigami.Units.smallSpacing * 2
        implicitHeight: Kirigami.Units.iconSizes.smallMedium
        Layout.minimumWidth: implicitWidth
        Layout.preferredWidth: implicitWidth
        Layout.minimumHeight: implicitHeight
        Layout.preferredHeight: implicitHeight

        onClicked: root.expanded = !root.expanded

        PlasmaComponents.ToolTip.text: Logic.TITLE + " · " + Logic.WINDOW_TITLES.rolling + " · "
            + Logic.statusText(root.lastError, root.updatedAt)
        PlasmaComponents.ToolTip.visible: containsMouse

        RowLayout {
            id: compactRow

            anchors.centerIn: parent
            spacing: Kirigami.Units.smallSpacing

            Kirigami.Icon {
                source: Plasmoid.icon || "speedometer"
                color: root.tierColor(Logic.colorTier(compact.percent))
                Layout.preferredWidth: Kirigami.Units.iconSizes.smallMedium
                Layout.preferredHeight: Kirigami.Units.iconSizes.smallMedium
            }

            PlasmaComponents.Label {
                text: Logic.percentText(compact.percent)
                color: root.tierColor(Logic.colorTier(compact.percent))
                font: Kirigami.Theme.smallFont
            }
        }

        // Процент 5-часового лимита: на панели показываем только его.
        readonly property real percent: root.rollingPercent
    }

    fullRepresentation: Item {
        id: full

        Layout.minimumWidth: Kirigami.Units.gridUnit * 17
        Layout.preferredWidth: Kirigami.Units.gridUnit * 17
        Layout.minimumHeight: column.implicitHeight + Kirigami.Units.largeSpacing * 2
        Layout.preferredHeight: Layout.minimumHeight

        ColumnLayout {
            id: column

            anchors.fill: parent
            anchors.margins: Kirigami.Units.largeSpacing
            spacing: Kirigami.Units.smallSpacing

            RowLayout {
                Layout.fillWidth: true
                spacing: 0

                PlasmaComponents.Label {
                    Layout.fillWidth: true
                    text: Logic.TITLE
                    font.bold: true
                    elide: Text.ElideRight
                }

                PlasmaComponents.ToolButton {
                    icon.name: "view-refresh"
                    enabled: !root.loading
                    onClicked: root.refresh()
                    PlasmaComponents.ToolTip.text: "Обновить сейчас"
                }

                PlasmaComponents.ToolButton {
                    icon.name: "settings-configure"
                    onClicked: Plasmoid.internalAction("configure").trigger()
                    PlasmaComponents.ToolTip.text: "Настройки"
                }
            }

            UsageBar {
                Layout.fillWidth: true
                title: Logic.WINDOW_TITLES.rolling
                item: root.usage ? root.usage.rolling : null
            }

            UsageBar {
                Layout.fillWidth: true
                title: Logic.WINDOW_TITLES.weekly
                item: root.usage ? root.usage.weekly : null
            }

            UsageBar {
                Layout.fillWidth: true
                title: Logic.WINDOW_TITLES.monthly
                item: root.usage ? root.usage.monthly : null
            }

            PlasmaComponents.Label {
                Layout.fillWidth: true
                text: Logic.statusText(root.lastError, root.updatedAt)
                color: root.lastError ? Kirigami.Theme.negativeTextColor : Kirigami.Theme.textColor
                opacity: 0.6
                font: Kirigami.Theme.smallFont
                elide: Text.ElideRight
            }
        }
    }
}
