from __future__ import annotations

OPERATOR_TABS_CARD = r"""
<nav class="operator-tabs" id="operatorTabs" aria-label="Operator workspace tabs">
  <button type="button" data-tab="mission" onclick="showOperatorTab('mission')">Миссия / Mission</button>
  <button type="button" data-tab="scenarios" onclick="showOperatorTab('scenarios')">Сценарии / Scenarios</button>
  <button type="button" data-tab="experiments" onclick="showOperatorTab('experiments')">Эксперименты / Experiments</button>
  <button type="button" data-tab="results" onclick="showOperatorTab('results')">Результаты / Results</button>
  <button type="button" data-tab="expert" onclick="showOperatorTab('expert')">Эксперт / Expert</button>
</nav>

<div class="card active-run-card" id="activeRunConfigurationCard">
  <h3>Активная расчётная конфигурация / Active Run Configuration</h3>
  <p class="hint">Контроль воспроизводимости: здесь всегда показана точная комбинация расчётных входов. Переключение рабочего пространства ничего не меняет скрыто.</p>
  <div class="active-run-grid">
    <div><b>Runnable Scenario</b><div id="activeScenario">—</div></div>
    <div><b>Force mode</b><div id="activeMode">—</div></div>
    <div><b>Force model</b><div id="activeForceModel">—</div></div>
    <div><b>Authority</b><div id="activeAuthority">—</div></div>
    <div><b>Design screening</b><div id="activeDesignScreening">—</div></div>
    <div><b>Design validation</b><div id="activeDesignValidation">—</div></div>
    <div><b>Design config</b><div id="activeDesignConfig">—</div></div>
    <div><b>Robustness validation</b><div id="activeRobustnessValidation">—</div></div>
    <div><b>Robustness config</b><div id="activeRobustnessConfig">—</div></div>
    <div><b>Force fingerprint</b><div><code id="activeFingerprint">—</code></div></div>
  </div>
  <div id="activeRunSummary" class="status">Загрузите ScenarioConfig / Load a ScenarioConfig.</div>
</div>

<div id="operatorTabMission" class="operator-tab-pane" data-tab-pane="mission">
  <div class="operator-input-intro">
    <h2>Миссия моделирования и исследования / Mission Workspace</h2>
    <p class="hint">Начинайте с инженерной цели, а не с формата файла. Миссия связывает baseline, производные сценарии, эксперименты, расчёты и выводы.</p>
  </div>
  <div class="card mission-control-card" id="missionControlCard">
    <h3>Эшелон выполнения AIMETON</h3>
    <div class="mission-echelon" id="missionEchelon">
      <button type="button" data-echelon="manual" onclick="setMissionEchelon('manual')">Ручной</button>
      <button type="button" data-echelon="assisted" onclick="setMissionEchelon('assisted')">Полуавтоматический</button>
      <button type="button" data-echelon="auto" onclick="setMissionEchelon('auto')">Автоматический</button>
    </div>
    <div id="missionModeNote" class="status"></div>
    <label><b>Цель исследования / Mission objective</b>
      <textarea id="missionObjective" rows="3" placeholder="Например: исследовать чувствительность удержания структуры ГЛОНАСС к модели ГПЗ 8x8…32x32 на 7 суток"></textarea>
    </label>
    <div class="grid">
      <label>Система
        <select id="missionSystem">
          <option value="GLONASS">ГЛОНАСС</option>
          <option value="GPS">GPS</option>
          <option value="Galileo">Galileo</option>
          <option value="BeiDou">BeiDou / Compass</option>
        </select>
      </label>
      <label>Дата baseline <input id="missionDate" type="date"></label>
    </div>
    <div class="mission-actions">
      <button type="button" onclick="missionPrepareBaseline()">Новый baseline из фактической ОГ</button>
      <button type="button" onclick="missionUseCurrentScenario()">Продолжить с текущим сценарием</button>
      <button type="button" onclick="missionPrepareVariant()">Создать вариант текущего сценария</button>
    </div>
    <div id="missionNextStep" class="status"></div>
  </div>
  <section class="operator-input-group" id="operatorMissionBaseline">
    <h3>Baseline из реальной орбитальной группировки</h3>
    <p class="hint">Нормальный путь — единый IGS/RINEX intake. Конкретный транспорт, cache, SHA-256 и provenance сохраняются автоматически; source-specific формы не засоряют рабочий маршрут.</p>
  </section>
</div>

<div id="operatorTabScenarios" class="operator-tab-pane" data-tab-pane="scenarios">
  <div class="operator-input-intro">
    <h2>Сценарии / Scenarios</h2>
    <p class="hint">Baseline и его производные варианты. Исходный ScenarioConfig не перезаписывается: изменение исследования создаёт новый вариант с lineage.</p>
  </div>
  <section class="operator-input-group" id="operatorScenarioOverview"><h3>Текущий сценарий</h3></section>
  <section class="operator-input-group" id="operatorScenarioVariants"><h3>Быстрые корректировки и варианты</h3></section>
  <section class="operator-input-group" id="operatorScenarioManualState"><h3>Сценарий из явного состояния КА</h3></section>
  <section class="operator-input-group" id="operatorScenarioSynthesis"><h3>Сценарий из синтетической ОГ</h3></section>
  <section class="operator-input-group" id="operatorScenarioBulk"><h3>Пакетный ввод параметров</h3></section>
</div>

<div id="operatorTabExperiments" class="operator-tab-pane" data-tab-pane="experiments">
  <div class="operator-input-intro">
    <h2>Эксперименты / Experiments</h2>
    <p class="hint">Изменяйте модель, управление и возмущения как производные эксперименты от выбранного baseline. Здесь находятся Design и Robustness, а не источники данных.</p>
  </div>
  <section class="operator-input-group" id="operatorExperimentModel"><h3>Модель и проектирование</h3></section>
  <section class="operator-input-group" id="operatorExperimentRobustness"><h3>Робастность и возмущения</h3></section>
</div>

<div id="operatorTabResults" class="operator-tab-pane" data-tab-pane="results">
  <div class="operator-input-intro">
    <h2>Расчёт, сравнение и результаты / Run, Compare & Results</h2>
    <p class="hint">Запуск, ход расчёта, ресурсы, продолжение завершённых расчётов и инженерные результаты. Следующий шаг исследования должен рождаться отсюда.</p>
  </div>
</div>

<div id="operatorTabExpert" class="operator-tab-pane" data-tab-pane="expert">
  <div class="operator-input-intro">
    <h2>Эксперт / Expert</h2>
    <p class="hint">Полный YAML, normalized state, source-specific adapters, ручные authority-инструменты и низкоуровневая диагностика. Это ручной контур, а не основной маршрут исследования.</p>
  </div>
</div>
"""

OPERATOR_TABS_STYLE = r"""
<style id="operatorTabsStyle">
.operator-tabs{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 14px 0;position:sticky;top:0;z-index:10;background:#f4f6f8;padding:8px 0}
.operator-tabs button{width:auto;margin:0;padding:9px 13px;border:1px solid #cbd3da;border-radius:7px;background:white}
.operator-tabs button.active{font-weight:700;outline:2px solid #17202a}
.operator-tab-pane{display:none}.operator-tab-pane.active{display:block}
.active-run-card{border-width:2px}.active-run-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px 18px}
.active-run-grid>div{min-width:0}.active-run-grid code{word-break:break-all;font-size:11px}
.workflow-split-card{border-left:4px solid #d9dee3}
.operator-input-intro{margin:0 0 12px 0}.operator-input-group{border:1px solid #d9dee3;border-radius:8px;padding:12px 14px;margin:0 0 14px 0;background:#f8fafb}.operator-input-group>h3{margin:0 0 4px 0}.operator-input-group>.card{margin-top:12px;margin-bottom:0;background:white}.operator-input-group:empty{display:none}
.mission-control-card{border-width:2px}.mission-echelon,.mission-actions{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0 12px}.mission-echelon button,.mission-actions button{width:auto}.mission-echelon button.active{font-weight:700;outline:2px solid #17202a}.mission-control-card textarea{width:100%;box-sizing:border-box}.mission-control-card .status{margin-top:8px}
@media(max-width:900px){.active-run-grid{grid-template-columns:1fr}.operator-tabs{position:static}}
</style>
"""

OPERATOR_TABS_SCRIPT = r"""
function operatorById(id){return document.getElementById(id);}
function operatorText(id,value){const e=operatorById(id);if(e)e.textContent=(value===undefined||value===null||value==='')?'—':String(value);}
function operatorSelectValue(id){const e=operatorById(id);return e&&e.value?e.value:'—';}
function normalizedOperatorTab(name){return ({inputs:'mission',design:'experiments',robustness:'experiments'}[name]||name);}
function activeRunRefresh(){
  const selected=operatorSelectValue('scenario');
  const normalized=(window.current&&current.normalized)||{};
  const force=normalized.force_model||{};
  operatorText('activeScenario',selected);
  operatorText('activeMode',current&&current.force_mode?current.force_mode:(force.mode||'—'));
  operatorText('activeForceModel',force.gravity_model?force.gravity_model+' '+force.gravity_degree+'x'+force.gravity_order:'—');
  operatorText('activeAuthority',current&&current.authority?current.authority:'—');
  operatorText('activeFingerprint',current&&current.force_model_fingerprint?current.force_model_fingerprint:'—');
  operatorText('activeDesignScreening',operatorSelectValue('designScreening'));
  operatorText('activeDesignValidation',operatorSelectValue('designValidation'));
  operatorText('activeDesignConfig',operatorSelectValue('designConfig'));
  operatorText('activeRobustnessValidation',operatorSelectValue('robustnessValidation'));
  operatorText('activeRobustnessConfig',operatorSelectValue('robustnessConfig'));
  const summary=operatorById('activeRunSummary');
  if(summary){
    const tab=normalizedOperatorTab(localStorage.getItem('operator-tab')||'mission');
    if(tab==='experiments')summary.textContent='EXPERIMENT: scenario='+selected+'; design='+operatorSelectValue('designConfig')+'; robustness='+operatorSelectValue('robustnessConfig');
    else summary.textContent='SCENARIO: '+selected+'; mode='+(current&&current.force_mode?current.force_mode:'—')+'; authority='+(current&&current.authority?current.authority:'—');
  }
}
function operatorMoveCard(id,target){const card=operatorById(id),pane=operatorById(target);if(card&&pane)pane.appendChild(card);}
function operatorAdoptCardByChild(childId,cardId){const child=operatorById(childId);if(!child)return;const card=child.closest('.card');if(card&&!card.id)card.id=cardId;}
function splitWorkflowCard(){
  const card=operatorById('workflowCard');if(!card)return;
  const nodes=Array.from(card.childNodes);let robust=false;
  const design=document.createElement('div');design.className='card workflow-split-card';design.id='designWorkflowCard';
  const robustness=document.createElement('div');robustness.className='card workflow-split-card';robustness.id='robustnessWorkflowCard';
  for(const node of nodes){
    if(node.nodeType===1&&node.tagName==='H4'&&String(node.textContent).includes('Robustness'))robust=true;
    (robust?robustness:design).appendChild(node);
  }
  card.replaceWith(design,robustness);
}
function splitConstellationEditorLegacyGravity(){
  const card=operatorById('constellationEditorCard');if(!card)return;
  const children=Array.from(card.childNodes);
  let legacy=false;
  const expert=document.createElement('div');expert.className='card workflow-split-card';expert.id='legacyConstellationGravityCard';
  for(const node of children){
    if(node.nodeType===1&&node.tagName==='H3'&&String(node.textContent).includes('Модель гравитационного поля Земли'))legacy=true;
    if(legacy)expert.appendChild(node);
  }
  if(expert.childNodes.length)card.after(expert);
}
function missionModeText(mode){
  if(mode==='manual')return 'РУЧНОЙ: никаких подстановок. Вы сами выбираете source, template и каждое инженерно значимое действие; Expert всегда доступен.';
  if(mode==='auto')return 'АВТО: программа выполняет только разрешённые однозначные шаги и показывает provenance. Для baseline активный ScenarioConfig используется как modelling authority; неоднозначность или несовместимость блокирует автоматическую цепочку.';
  return 'ПОЛУАВТО: программа предлагает и подставляет безопасные значения, но инженер подтверждает значимые переходы. Это рекомендуемый рабочий эшелон.';
}
function setMissionEchelon(mode){
  if(!['manual','assisted','auto'].includes(mode))mode='assisted';
  localStorage.setItem('mission-echelon',mode);
  document.querySelectorAll('#missionEchelon [data-echelon]').forEach(b=>b.classList.toggle('active',b.dataset.echelon===mode));
  const note=operatorById('missionModeNote');if(note)note.textContent=missionModeText(mode);
  missionRefreshNextStep();
}
function missionRefreshNextStep(text){
  const e=operatorById('missionNextStep');if(!e)return;
  if(text){e.textContent=text;return;}
  const selected=operatorSelectValue('scenario');
  const mode=localStorage.getItem('mission-echelon')||'assisted';
  e.textContent=selected==='—'?'Следующий шаг: создайте baseline миссии.':'Следующий шаг: '+(mode==='manual'?'проверьте ScenarioConfig и выберите действие вручную.':'создайте вариант/эксперимент от '+selected+'.');
}
function missionUseCurrentScenario(){showOperatorTab('scenarios');const e=operatorById('scenarioSummaryCard');if(e)e.scrollIntoView({behavior:'smooth',block:'start'});}
function missionPrepareVariant(){showOperatorTab('scenarios');if(typeof syncScenarioVariant==='function')syncScenarioVariant();const e=operatorById('scenarioVariantCard');if(e)e.scrollIntoView({behavior:'smooth',block:'start'});}
async function missionPrepareBaseline(){
  const date=(operatorById('missionDate')||{}).value||'';
  const system=(operatorById('missionSystem')||{}).value||'GLONASS';
  if(!date){missionRefreshNextStep('Укажите дату baseline.');return;}
  if(window.igsStartDate)igsStartDate.value=date;if(window.igsSystem)igsSystem.value=system;
  const mode=localStorage.getItem('mission-echelon')||'assisted';
  const selected=window.scenario&&scenario.value?scenario.value:'';
  if(mode!=='manual'&&window.igsTemplateScenario&&selected)igsTemplateScenario.value=selected;
  const card=operatorById('igsConstellationCard');if(card)card.scrollIntoView({behavior:'smooth',block:'start'});
  if(mode==='manual'){missionRefreshNextStep('Ручной эшелон: дата и система подготовлены. Выберите modelling template и выполните два шага IGS вручную.');return;}
  if(mode==='assisted'){missionRefreshNextStep(selected?'Полуавтоматический эшелон: активный сценарий предложен как modelling template. Проверьте и подтвердите загрузку/формирование baseline.':'Полуавтоматический эшелон: выберите modelling template, затем подтвердите два шага baseline.');return;}
  if(!selected){missionRefreshNextStep('AUTO остановлен: нет активного ScenarioConfig для modelling authority. Выберите базовую модель или перейдите в ручной/полуавтоматический эшелон.');return;}
  missionRefreshNextStep('AUTO: IGS intake → cache → '+selected+' authority → derived ScenarioConfig…');
  if(typeof fetchIgsConstellationData!=='function'||typeof buildIgsConstellation!=='function'){missionRefreshNextStep('AUTO остановлен: IGS workflow недоступен.');return;}
  await fetchIgsConstellationData();
  if(operatorById('igsConstellationFetchStatus')&&igsConstellationFetchStatus.classList.contains('danger')){missionRefreshNextStep('AUTO остановлен на получении IGS. Подробности показаны в baseline-карточке.');return;}
  await buildIgsConstellation();
  if(operatorById('igsConstellationStatus')&&igsConstellationStatus.classList.contains('danger')){missionRefreshNextStep('AUTO остановлен при построении runnable ScenarioConfig. Подробности показаны в baseline-карточке.');return;}
  missionRefreshNextStep('AUTO baseline готов. Следующий шаг: создать вариант или перейти к экспериментам.');
}
function installMissionWorkspace(){
  const objective=operatorById('missionObjective');if(objective){objective.value=localStorage.getItem('mission-objective')||'';objective.addEventListener('input',()=>localStorage.setItem('mission-objective',objective.value));}
  setMissionEchelon(localStorage.getItem('mission-echelon')||'assisted');
  missionRefreshNextStep();
}
function arrangeOperatorTabs(){
  const section=document.querySelector('main section');if(!section)return;
  splitWorkflowCard();splitConstellationEditorLegacyGravity();
  ['operatorTabMission','operatorTabScenarios','operatorTabExperiments','operatorTabResults','operatorTabExpert'].forEach(id=>{const pane=operatorById(id);if(pane&&pane.parentElement!==section)section.appendChild(pane);});

  operatorAdoptCardByChild('title','scenarioSummaryCard');
  operatorAdoptCardByChild('fleet','constellationSummaryCard');
  operatorAdoptCardByChild('geometry','geometrySummaryCard');
  operatorAdoptCardByChild('operations','operationsSummaryCard');
  operatorAdoptCardByChild('yaml','expertYamlCard');
  operatorAdoptCardByChild('normalized','normalizedScenarioCard');

  ['igsConstellationCard'].forEach(id=>operatorMoveCard(id,'operatorMissionBaseline'));
  ['scenarioSummaryCard','constellationSummaryCard','geometrySummaryCard','constellationEditorCard'].forEach(id=>operatorMoveCard(id,'operatorScenarioOverview'));
  ['scenarioVariantCard'].forEach(id=>operatorMoveCard(id,'operatorScenarioVariants'));
  ['osculatingCard'].forEach(id=>operatorMoveCard(id,'operatorScenarioManualState'));
  ['walkerCard'].forEach(id=>operatorMoveCard(id,'operatorScenarioSynthesis'));
  ['workbookImportCard','spacecraftCatalogCard'].forEach(id=>operatorMoveCard(id,'operatorScenarioBulk'));

  ['gravityModelCard','closedLoopCard','designWorkflowCard','optimalOperationsCard'].forEach(id=>operatorMoveCard(id,'operatorExperimentModel'));
  ['perturbationCard','robustnessWorkflowCard'].forEach(id=>operatorMoveCard(id,'operatorExperimentRobustness'));
  ['operationsSummaryCard','runProgressCard','runPromotionCard','resourceStateCard','driftConsistencyCard'].forEach(id=>operatorMoveCard(id,'operatorTabResults'));
  ['galileoGscCard','iacGnssCard','glonassAlmanacCard','gnssAlmanacCard','noradCard','glonassRinexRunnerCard','iacGlonassRunnerCard','iacGlonassConstellationCard','navcenGpsRunnerCard','mixedGnssRunnerCard','scenarioEditorCard','legacyConstellationGravityCard','expertYamlCard','normalizedScenarioCard'].forEach(id=>operatorMoveCard(id,'operatorTabExpert'));

  const active=normalizedOperatorTab(localStorage.getItem('operator-tab')||'mission');showOperatorTab(active);
}
function showOperatorTab(name){
  name=normalizedOperatorTab(name);
  document.querySelectorAll('[data-tab-pane]').forEach(p=>p.classList.toggle('active',p.dataset.tabPane===name));
  document.querySelectorAll('#operatorTabs [data-tab]').forEach(b=>b.classList.toggle('active',b.dataset.tab===name));
  localStorage.setItem('operator-tab',name);activeRunRefresh();missionRefreshNextStep();
}
function installActiveRunListeners(){
  ['scenario','designScreening','designValidation','designConfig','robustnessValidation','robustnessConfig'].forEach(id=>{const e=operatorById(id);if(e)e.addEventListener('change',()=>{activeRunRefresh();missionRefreshNextStep();});});
}
const operatorOriginalLoadScenario=loadScenario;
loadScenario=async function(){await operatorOriginalLoadScenario();activeRunRefresh();missionRefreshNextStep();if(typeof syncScenarioVariant==='function')syncScenarioVariant();};
const operatorTabsBootstrap=bootstrap;
bootstrap=async function(){await operatorTabsBootstrap();arrangeOperatorTabs();installActiveRunListeners();installMissionWorkspace();activeRunRefresh();};
"""