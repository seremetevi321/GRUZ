// Тонкий клиент: вся логика денег и прав — на сервере.
// Оболочка не регистрирует ни одной команды IPC и ни одного плагина,
// поэтому JavaScript в окне не может обратиться к файлам, процессам или сети в обход CSP.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    tauri::Builder::default()
        .run(tauri::generate_context!())
        .expect("не удалось запустить приложение");
}
