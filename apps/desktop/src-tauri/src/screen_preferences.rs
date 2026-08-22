use serde::Serialize;

use crate::windows_credential::WindowsCredentialStore;

pub const SCREEN_PREFERENCE_CREDENTIAL_TARGET: &str = "DaonUser/ScreenPreferences/v1";

#[derive(Serialize)]
pub struct ScreenPreferenceProjection {
    theme: String,
}

impl ScreenPreferenceProjection {
    fn new(theme: String) -> Result<Self, &'static str> {
        if !matches!(theme.as_str(), "system" | "light" | "dark") { return Err("SCREEN_PREFERENCE_INVALID"); }
        Ok(Self { theme })
    }
}

fn store() -> WindowsCredentialStore {
    WindowsCredentialStore::new(SCREEN_PREFERENCE_CREDENTIAL_TARGET.to_owned())
}

#[tauri::command]
pub fn screen_preferences_get() -> Result<ScreenPreferenceProjection, &'static str> {
    ScreenPreferenceProjection::new(store().read_screen_preference()?.unwrap_or_else(|| "system".to_owned()))
}

#[tauri::command]
pub fn screen_preferences_save(theme: String) -> Result<ScreenPreferenceProjection, &'static str> {
    let projection = ScreenPreferenceProjection::new(theme)?;
    store().write_screen_preference(&projection.theme)?;
    Ok(projection)
}

#[tauri::command]
pub fn screen_preferences_reset() -> Result<ScreenPreferenceProjection, &'static str> {
    store().revoke_screen_preference()?;
    screen_preferences_get()
}
