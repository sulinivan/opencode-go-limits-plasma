import QtQuick
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.plasma.components as PlasmaComponents
import "../code/logic.js" as Logic

// Одна строка лимита: название, процент, полоса и время сброса.
// Компонент ничего не знает о сети — получает готовый объект окна лимита.
ColumnLayout {
    id: bar

    property var item: null
    property string title: ""
    property double nowMs: Date.now()

    readonly property real percent: (item && typeof item.percent === "number") ? item.percent : -1
    readonly property string tier: Logic.colorTier(bar.percent)
    readonly property color barColor: bar.tier === "crit" ? Kirigami.Theme.negativeTextColor
        : bar.tier === "warn" ? Kirigami.Theme.neutralTextColor
        : bar.tier === "ok" ? Kirigami.Theme.positiveTextColor
        : Kirigami.Theme.disabledTextColor

    spacing: Math.round(Kirigami.Units.smallSpacing / 2)

    // Пересчитывает подписи сброса, не трогая сеть.
    Timer {
        interval: 30000
        running: true
        repeat: true
        onTriggered: bar.nowMs = Date.now()
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: Kirigami.Units.smallSpacing

        PlasmaComponents.Label {
            Layout.fillWidth: true
            text: bar.title
            opacity: 0.7
            font: Kirigami.Theme.smallFont
            elide: Text.ElideRight
        }

        PlasmaComponents.Label {
            text: Logic.percentText(bar.percent)
            color: bar.barColor
            font: Kirigami.Theme.smallFont
        }
    }

    Rectangle {
        id: track

        Layout.fillWidth: true
        Layout.preferredHeight: Math.max(4, Kirigami.Units.smallSpacing)
        radius: height / 2
        color: Kirigami.Theme.alternateBackgroundColor

        Rectangle {
            id: fill

            height: track.height
            radius: track.radius
            color: bar.barColor
            width: bar.percent <= 0 ? 0 : Math.round(track.width * Math.min(bar.percent, 100) / 100)
        }

        MouseArea {
            id: barHover

            anchors.fill: parent
            hoverEnabled: true
            acceptedButtons: Qt.NoButton

            PlasmaComponents.ToolTip.text: Logic.formatResetFull(bar.item ? bar.item.resetsAt : "", bar.nowMs)
            PlasmaComponents.ToolTip.visible: barHover.containsMouse && bar.percent >= 0
        }
    }

    PlasmaComponents.Label {
        Layout.fillWidth: true
        text: bar.percent < 0 ? "" : Logic.resetText(bar.item, bar.nowMs)
        opacity: 0.6
        font: Kirigami.Theme.smallFont
        elide: Text.ElideRight
    }
}
