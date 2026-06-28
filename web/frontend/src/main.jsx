import React, {useEffect, useState} from 'react';
import {createRoot} from 'react-dom/client';
import './style.css';

const formatError = detail => {
  if (!detail) return 'Неизвестная ошибка';
  if (typeof detail === 'string') {
    return detail;
  }
  if (Array.isArray(detail)) return detail.map(item => item.msg || JSON.stringify(item)).join('; ');
  if (typeof detail === 'object') return detail.msg || detail.detail || JSON.stringify(detail);
  return String(detail);
};

const api = async (path, options = {}) => {
  const response = await fetch(path, {
    headers: {'Content-Type': 'application/json'},
    ...options,
  });
  if (response.status === 204) return null;
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(formatError(body.detail || body));
  return body;
};

const profileLabel = name => name === 'autotune' ? 'Autotune' : (name || '—');

const SERVICE_PRESETS = [
  {name: 'YouTube', hint: 'web · API · превью · видео', domains: ['www.youtube.com', 'youtubei.googleapis.com', 'i.ytimg.com', 'redirector.googlevideo.com']},
  {name: 'X', hint: 'web · API · фото · видео', domains: ['x.com', 'api.x.com', 'abs.twimg.com', 'pbs.twimg.com', 'video.twimg.com']},
  {name: 'Instagram', hint: 'web · API · CDN · чат', domains: ['www.instagram.com', 'i.instagram.com', 'graph.instagram.com', 'static.cdninstagram.com', 'edge-chat.instagram.com']},
  {name: 'Discord', hint: 'web · gateway · CDN · media', domains: ['discord.com', 'gateway.discord.gg', 'cdn.discordapp.com', 'media.discordapp.net']},
  {name: 'Facebook', hint: 'web · API · static · video', domains: ['www.facebook.com', 'graph.facebook.com', 'static.xx.fbcdn.net', 'video.xx.fbcdn.net']},
  {name: 'Signal', hint: 'web · chat · groups · calls', domains: ['signal.org', 'chat.signal.org', 'signal.group', 'sfu.voip.signal.org']},
  {name: 'LinkedIn', hint: 'web · API · media · static', domains: ['www.linkedin.com', 'api.linkedin.com', 'media.licdn.com', 'static.licdn.com']},
];

const formatDuration = seconds => {
  const value = Math.max(0, Math.round(seconds || 0));
  const minutes = Math.floor(value / 60);
  return `${minutes}:${String(value % 60).padStart(2, '0')}`;
};

function Meter({label, value}) {
  return <div className="meter"><div><span>{label}</span><strong>{value ?? '—'}%</strong></div><div className="track"><i style={{width: `${value || 0}%`}}/></div></div>;
}

function Autotune({run, setRun, onMessage, onReload}) {
  const [domains, setDomains] = useState(() => localStorage.getItem('autotuneDomains') || 'rutracker.org');
  const [repeats, setRepeats] = useState(() => localStorage.getItem('autotuneRepeats') || '2');
  const [scanLevel, setScanLevel] = useState(() => localStorage.getItem('autotuneScanLevel') || 'quick');
  const [busy, setBusy] = useState(false);
  const [updatedAt, setUpdatedAt] = useState(null);
  const [selected, setSelected] = useState({});
  const running = run && ['queued', 'running'].includes(run.status);
  const elapsed = run?.started_at ? (Date.now() - new Date(run.started_at).getTime()) / 1000 : 0;
  const candidates = run?.candidates || [];

  useEffect(() => localStorage.setItem('autotuneDomains', domains), [domains]);
  useEffect(() => localStorage.setItem('autotuneRepeats', repeats), [repeats]);
  useEffect(() => localStorage.setItem('autotuneScanLevel', scanLevel), [scanLevel]);
  useEffect(() => { if (run) setUpdatedAt(new Date()); }, [run?.status, run?.phase, run?.progress, run?.tested]);
  useEffect(() => {
    if (run?.status === 'completed') {
      setSelected(Object.fromEntries((run.results || []).map(item => [item.protocol, item.strategy])));
    }
  }, [run?.id, run?.status]);

  const addPreset = preset => {
    const current = domains.split(/[\s,]+/).map(value => value.trim()).filter(Boolean);
    const combined = [...new Set([...current, ...preset.domains])];
    if (combined.length > 30) onMessage('Можно проверить не более 30 доменов; лишние адреса не добавлены');
    setDomains(combined.slice(0, 30).join('\n'));
  };

  const start = async () => {
    const parsedDomains = domains.split(/[\s,]+/).map(d => d.trim()).filter(Boolean);
    if (!parsedDomains.length) {
      onMessage('Укажите хотя бы один домен');
      return;
    }
    setBusy(true);
    try {
      const body = {domains: parsedDomains, protocols: ['http', 'https', 'quic'], repeats: Number(repeats), scan_level: scanLevel, test_set: 'auto'};
      setRun(await api('/api/v1/autotune/runs', {method: 'POST', body: JSON.stringify(body)}));
      onMessage('Автоподбор запущен. Это может занять несколько минут.');
    } catch (e) {
      onMessage(e.message);
    } finally {
      setBusy(false);
    }
  };
  const refresh = async () => {
    try {
      setRun(await api('/api/v1/autotune/runs/current'));
      onMessage('Статус автоподбора обновлён');
    } catch (e) {
      onMessage(e.message);
    }
  };
  const apply = async () => {
    const selections = Object.entries(selected).map(([protocol, strategy]) => ({protocol, strategy}));
    if (!selections.length) {
      onMessage('Отметьте хотя бы одну стратегию');
      return;
    }
    setBusy(true);
    try {
      await api(`/api/v1/autotune/runs/${run.id}/apply`, {method: 'POST', body: JSON.stringify({selections})});
      onMessage('Отмеченные стратегии применены');
      await onReload();
    } catch (e) {
      onMessage(e.message);
    } finally {
      setBusy(false);
    }
  };
  const cancel = async () => {
    setBusy(true);
    try {
      setRun(await api(`/api/v1/autotune/runs/${run.id}/cancel`, {method: 'POST'}));
      onMessage('Автоподбор остановлен, zapret2 восстановлен');
      await onReload();
    } catch (e) {
      onMessage(e.message);
    } finally {
      setBusy(false);
    }
  };

  return <section className="card autotune"><div className="title"><div><h2>Автоподбор</h2><p>blockcheck2 проверяет стратегии через текущего провайдера. Во время теста zapret2 временно останавливается и затем восстанавливается.</p></div>{run && <span className={`run-status ${run.status}`}>{run.status}</span>}</div>
    <label>Проверяемые домены <small>сохраняются в этом браузере</small><textarea rows="2" value={domains} onChange={e => setDomains(e.target.value)}/></label>
    <div className="presets">{SERVICE_PRESETS.map(preset => <button className="secondary" disabled={running} key={preset.name} onClick={() => addPreset(preset)}><b>+ {preset.name}</b><small>{preset.hint}</small></button>)}<button className="secondary clear-preset" disabled={running} onClick={() => setDomains('')}><b>Очистить</b><small>начать новый набор</small></button></div>
    <div className="tune-options"><label>Повторы<select value={repeats} onChange={e => setRepeats(e.target.value)}><option>1</option><option>2</option><option>3</option><option>4</option><option>5</option></select></label><label>Глубина<select value={scanLevel} onChange={e => setScanLevel(e.target.value)}><option value="quick">Быстро</option><option value="standard">Стандартно</option><option value="force">Полный перебор</option></select></label></div>
    <p className="depth-note">{scanLevel === 'quick' ? 'Быстрый режим: до 20 отобранных стратегий на домен.' : `Upstream-перебор: много сотен вариантов, лимит ${scanLevel === 'standard' ? '45' : '90'} минут.`}</p>
    <div className="actions"><button disabled={busy || running} onClick={start}>{running ? 'Тестирование идёт…' : 'Запустить автоподбор'}</button>{running && <button className="danger" disabled={busy} onClick={cancel}>Остановить</button>}<button className="secondary" disabled={busy} onClick={refresh}>Обновить статус</button></div>
    {running && <div className="live"><i/><div><b>{run.current_test ? `${run.current_test.protocol.toUpperCase()} · ${run.current_test.domain}` : (run.phase || 'подготовка')}</b><span>Проверка {run.tested || 0}{run.expected_tests ? ` из максимум ${run.expected_tests}` : ''} · прошло {formatDuration(elapsed)} из {formatDuration(run.limit_seconds)}</span>{run.current_test && <code>{run.current_test.strategy}</code>}</div></div>}
    {run && <div className="run">{run.expected_tests && <div className="run-progress"><i style={{width: `${run.progress || 0}%`}}/></div>}<div className="run-meta"><span>{run.phase || run.status}{run.expected_tests ? ` · ${run.progress || 0}%` : ` · ${run.tested || 0} проверок`}</span><span>{updatedAt ? `обновлено ${updatedAt.toLocaleTimeString()}` : 'ожидание обновления'}</span></div>{run.error && <p className="error">{run.error}</p>}{run.note && <p className="note">{run.note}</p>}
      {candidates.length > 0 && <div className="candidate-list"><h3>Рейтинг проверенных стратегий</h3><p>Автоподбор отмечает лучшую стратегию каждого протокола. Отметку можно снять или перенести на другой вариант.</p>{candidates.map(item => {
        const chosen = selected[item.protocol] === item.strategy;
        return <div className={`candidate ${chosen ? 'chosen' : ''}`} key={`${item.protocol}:${item.strategy}`}><label className="candidate-check"><input type="checkbox" checked={chosen} disabled={!item.successes || running} onChange={() => setSelected(current => {
          const next = {...current};
          if (chosen) delete next[item.protocol]; else next[item.protocol] = item.strategy;
          return next;
        })}/><span>{chosen ? 'Выбрана' : 'Выбрать'}</span></label><b>{item.protocol.toUpperCase()}</b><strong>{item.suitability}%</strong><span>{item.successes}/{item.attempts} попыток · домены {Math.round(item.coverage * 100)}%{chosen ? ' · используется' : ''}</span><code>{item.strategy}</code></div>;
      })}{run.status === 'completed' && <button className="apply-selected" disabled={busy || !Object.keys(selected).length} onClick={apply}>Применить отмеченные стратегии</button>}</div>}</div>}
  </section>;
}

function App() {
  const [status, setStatus] = useState(null);
  const [wifi, setWifi] = useState(null);
  const [zapret, setZapret] = useState(null);
  const [autotune, setAutotune] = useState(null);
  const [logs, setLogs] = useState([]);
  const [message, setMessage] = useState('');
  const load = async () => {
    try {
      const [s, w, z, a] = await Promise.all([api('/api/v1/status'), api('/api/v1/wifi'), api('/api/v1/zapret/profiles'), api('/api/v1/autotune/runs/current')]);
      setStatus(s);
      setWifi({...w, password: ''});
      setZapret(z);
      setAutotune(a);
    } catch (e) {
      setMessage(e.message);
    }
  };
  useEffect(() => {
    load();
    let cancelled = false;
    let timer;
    const poll = async () => {
      try { setStatus(await api('/api/v1/status')); } catch {}
      if (!cancelled) timer = setTimeout(poll, 10000);
    };
    timer = setTimeout(poll, 10000);
    return () => { cancelled = true; clearTimeout(timer); };
  }, []);
  useEffect(() => {
    if (!autotune || !['queued', 'running'].includes(autotune.status)) return;
    let cancelled = false;
    let timer;
    const poll = async () => {
      try { setAutotune(await api('/api/v1/autotune/runs/current')); } catch (e) { setMessage(e.message); }
      if (!cancelled) timer = setTimeout(poll, 5000);
    };
    timer = setTimeout(poll, 5000);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [autotune?.status]);

  const saveWifi = async e => {
    e.preventDefault();
    try {
      const body = {ssid: wifi.ssid, channel: Number(wifi.channel), ...(wifi.password ? {password: wifi.password} : {})};
      setWifi({...await api('/api/v1/wifi', {method: 'PUT', body: JSON.stringify(body)}), password: ''});
      setMessage('Настройки Wi‑Fi применены');
    } catch (e) {
      setMessage(e.message);
    }
  };
  const setProfile = async profile => {
    try {
      await api('/api/v1/zapret/profile', {method: 'PUT', body: JSON.stringify({profile})});
      await load();
      setMessage('Профиль применён');
    } catch (e) {
      setMessage(e.message);
    }
  };
  const toggle = async () => {
    try {
      await api('/api/v1/zapret/enabled', {method: 'PUT', body: JSON.stringify({enabled: !zapret.enabled})});
      await load();
    } catch (e) {
      setMessage(e.message);
    }
  };
  const showLogs = async () => {
    try {
      setLogs((await api('/api/v1/zapret/logs?lines=150')).lines);
    } catch (e) {
      setMessage(e.message);
    }
  };
  return <div className="shell"><header><div><span className="mark">Z</span><div><b>zapret·rpi</b><small>локальный шлюз</small></div></div></header><main>
    <section className="hero"><div><span className="eyebrow">СИСТЕМА В СЕТИ</span><h1>Шлюз под контролем.</h1><p>Активная стратегия: <b>{profileLabel(status?.active_strategy)}</b></p></div><div className={`pulse ${status?.zapret_enabled ? 'on' : ''}`}>{status?.zapret_enabled ? 'zapret2 включён' : 'zapret2 выключен'}</div></section>
    {message && <div className="notice" onClick={() => setMessage('')}>{message}<span>×</span></div>}
    <div className="grid"><section className="card stats"><h2>Ресурсы</h2><Meter label="CPU" value={status?.cpu_percent}/><Meter label="Память" value={status?.memory_percent}/><div className="strategy"><span>Стратегия</span><b>{profileLabel(status?.active_strategy)}</b></div></section>
    <section className="card"><div className="title"><div><h2>zapret2</h2><p>Профиль обработки трафика</p></div><button className={zapret?.enabled ? 'switch on' : 'switch'} onClick={toggle}><i/></button></div><div className="profiles">{zapret?.profiles.map(p => <button key={p.name} className={p.name === zapret.active ? 'selected' : ''} onClick={() => setProfile(p.name)}><b>{profileLabel(p.name)}</b><small>{p.description}</small></button>)}</div>{zapret?.profiles.find(p => p.name === zapret.active)?.rules?.length > 0 && <div className="active-rules"><small>Сейчас используются правила</small>{zapret.profiles.find(p => p.name === zapret.active).rules.map((rule, index) => <code key={index}>{rule}</code>)}</div>}<button className="secondary" onClick={showLogs}>Обновить логи</button></section>
    <section className="card"><h2>Wi‑Fi</h2><p>Изменение кратко отключит точку доступа.</p>{wifi && <form onSubmit={saveWifi}><label>SSID<input value={wifi.ssid} onChange={e => setWifi({...wifi, ssid: e.target.value})}/></label><label>Новый пароль <small>оставьте пустым, чтобы не менять</small><input type="password" value={wifi.password} onChange={e => setWifi({...wifi, password: e.target.value})}/></label><label>Канал<select value={wifi.channel} onChange={e => setWifi({...wifi, channel: e.target.value})}><option>1</option><option>6</option><option>11</option></select></label><button>Применить Wi‑Fi</button></form>}</section>
    <section className="card clients"><div className="title"><div><h2>Клиенты Wi‑Fi</h2><p>Подключённые станции с данными DHCP</p></div><strong>{status?.clients.length || 0}</strong></div><div className="table">{status?.clients.length ? status.clients.map(c => <div key={c.mac}><span>{c.hostname || 'Неизвестное устройство'}<small>{c.mac}</small></span><b>{c.ip}</b></div>) : <p className="empty">Подключённых клиентов пока нет</p>}</div></section>
    <Autotune run={autotune} setRun={setAutotune} onMessage={setMessage} onReload={load}/></div>
    {logs.length > 0 && <section className="card logs"><div className="title"><h2>Логи zapret2</h2><button className="ghost" onClick={() => setLogs([])}>Закрыть</button></div><pre>{logs.join('\n')}</pre></section>}
  </main><footer>Raspberry Pi 3B · zapret-rpi {status?.project_version || '—'} · zapret2 {status?.revision?.slice(0, 8) || '—'}</footer></div>;
}
createRoot(document.getElementById('root')).render(<App/>);
