import QtQuick
import org.kde.kirigami as Kirigami
import org.kde.plasma.components as PlasmaComponents
import "../code/logic.js" as Logic

// Страница настроек виджета. Plasma сама связывает cfg_* свойства
// с записями из contents/config/main.xml.
Kirigami.FormLayout {

    property alias cfg_refreshMinutes: interval.value
    property alias cfg_notify: notifyBox.checked
    property alias cfg_notifyThreshold: threshold.value
    property alias cfg_panelColorSource: colorSource.currentValue
    property alias cfg_panelValueSource: valueSource.currentValue
    property alias cfg_notifyRolling: notifyRolling.checked
    property alias cfg_notifyDaily: notifyDaily.checked
    property alias cfg_notifyWeekly: notifyWeekly.checked
    property alias cfg_notifyMonthly: notifyMonthly.checked

    readonly property var windowOptions: [
        { text: Logic.WINDOW_TITLES.rolling, value: "rolling" },
        { text: Logic.WINDOW_TITLES.daily, value: "daily" },
        { text: Logic.WINDOW_TITLES.weekly, value: "weekly" },
        { text: Logic.WINDOW_TITLES.monthly, value: "monthly" }
    ]

    PlasmaComponents.SpinBox {
        id: interval

        from: 1
        to: 120
        Kirigami.FormData.label: "Интервал обновления (мин):"
    }

    PlasmaComponents.CheckBox {
        id: notifyBox

        text: "Уведомлять о превышении порога"
    }

    PlasmaComponents.CheckBox {
        id: notifyRolling

        text: Logic.WINDOW_TITLES.rolling
        enabled: notifyBox.checked
    }

    PlasmaComponents.CheckBox {
        id: notifyDaily

        text: Logic.WINDOW_TITLES.daily
        enabled: notifyBox.checked
    }

    PlasmaComponents.CheckBox {
        id: notifyWeekly

        text: Logic.WINDOW_TITLES.weekly
        enabled: notifyBox.checked
    }

    PlasmaComponents.CheckBox {
        id: notifyMonthly

        text: Logic.WINDOW_TITLES.monthly
        enabled: notifyBox.checked
    }

    PlasmaComponents.SpinBox {
        id: threshold

        from: 50
        to: 100
        enabled: notifyBox.checked
        Kirigami.FormData.label: "Порог, %:"
    }

    PlasmaComponents.ComboBox {
        id: colorSource

        Kirigami.FormData.label: "Цвет панели по лимиту:"
        textRole: "text"
        valueRole: "value"
        model: windowOptions
    }

    PlasmaComponents.ComboBox {
        id: valueSource

        Kirigami.FormData.label: "Значение на панели:"
        textRole: "text"
        valueRole: "value"
        model: windowOptions
    }
}
