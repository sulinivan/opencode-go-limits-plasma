import QtQuick
import org.kde.kirigami as Kirigami
import org.kde.plasma.components as PlasmaComponents

// Страница настроек виджета. Plasma сама связывает cfg_* свойства
// с записями из contents/config/main.xml.
Kirigami.FormLayout {

    property alias cfg_refreshMinutes: interval.value
    property alias cfg_notify: notifyBox.checked
    property alias cfg_notifyThreshold: threshold.value

    PlasmaComponents.SpinBox {
        id: interval

        from: 1
        to: 120
        Kirigami.FormData.label: "Интервал обновления (мин):"
    }

    PlasmaComponents.CheckBox {
        id: notifyBox

        text: "Уведомлять о 5-часовом лимите при превышении порога"
    }

    PlasmaComponents.SpinBox {
        id: threshold

        from: 50
        to: 100
        enabled: notifyBox.checked
        Kirigami.FormData.label: "Порог, %:"
    }
}
