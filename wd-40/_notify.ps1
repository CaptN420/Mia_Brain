# _notify.ps1 - show a Windows toast notification (no window, no install).
# Usage: powershell -NoProfile -WindowStyle Hidden -File _notify.ps1 "TITLE" "BODY" ["info|warning|error"]
param(
    [string]$Title = "wd-40 Shield",
    [string]$Body  = "",
    [string]$Level = "info"
)
try {
    # Load the Windows Runtime toast API via COM (no external module needed)
    $null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
    $null = [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime]

    $appId = "CaptN-BRAIN.wd40.Shield"
    $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(
        [Windows.UI.Notifications.ToastTemplateType]::ToastText02)
    $texts = $template.GetElementsByTagName("text")
    $texts.Item(0).AppendChild($template.CreateTextNode($Title)) | Out-Null
    $texts.Item(1).AppendChild($template.CreateTextNode($Body))  | Out-Null

    $toast = [Windows.UI.Notifications.ToastNotification]::new($template)
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
} catch {
    # Fallback: write to the daemon log so there is still a signal
    Add-Content -Path "$PSScriptRoot\logs\daemon.log" -Value ("[notify] (toast unavailable) " + $Title + ": " + $Body)
}
