from __future__ import annotations

from constellation_control.preview.source_settings import SOURCE_SETTINGS_CARD, SOURCE_SETTINGS_SCRIPT

SOURCE_SETTINGS_PANE = (
    '<div id="operatorTabSettings" class="operator-tab-pane" data-tab-pane="settings">'
    '<div class="operator-input-intro"><h2>Настройки / Settings</h2>'
    '<p class="hint">Локальная конфигурация транспортов и источников. Изменение настройки не меняет уже созданные сценарии и результаты.</p></div>'
    + SOURCE_SETTINGS_CARD
    + '</div>'
)

SOURCE_SETTINGS_TAB_SCRIPT = SOURCE_SETTINGS_SCRIPT + r"""
function installSourceSettingsTab(){
  const nav=document.getElementById('operatorTabs');
  if(nav&&!nav.querySelector('[data-tab="settings"]')){
    const button=document.createElement('button');
    button.type='button';button.dataset.tab='settings';button.textContent='Настройки / Settings';
    button.addEventListener('click',()=>showOperatorTab('settings'));
    nav.appendChild(button);
  }
}
"""
