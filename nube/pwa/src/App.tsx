import React, { useState, useEffect } from 'react';
import { 
  Home, 
  FileText, 
  TrendingUp, 
  DollarSign, 
  LogOut, 
  RefreshCw, 
  AlertTriangle,
  Clock,
  Calendar,
  Layers
} from 'lucide-react';
import { 
  ResponsiveContainer, 
  BarChart, 
  Bar, 
  XAxis, 
  YAxis, 
  Tooltip, 
  CartesianGrid 
} from 'recharts';
import { 
  insforge, 
  getKPIs, 
  getPendientes, 
  getVentas, 
  getFlujo, 
  type KPIResult, 
  type FacturaPendiente, 
  type VentasResult, 
  type FlujoResult 
} from './api';

// Helper para dar formato de peso chileno (CLP) sin decimales
const formatCLP = (val: number) => {
  return new Intl.NumberFormat('es-CL', {
    style: 'currency',
    currency: 'CLP',
    minimumFractionDigits: 0,
    maximumFractionDigits: 0
  }).format(val);
};

// Helper para formatear fechas legibles
const formatDate = (dateStr: string) => {
  if (!dateStr) return '';
  const d = new Date(dateStr);
  return d.toLocaleDateString('es-CL', { timeZone: 'UTC', day: '2-digit', month: '2-digit', year: 'numeric' });
};

// Helper para formatear fechas con hora
const formatDateTime = (dateStr: string) => {
  if (!dateStr) return '';
  const d = new Date(dateStr);
  return d.toLocaleDateString('es-CL', { 
    day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit'
  });
};

export default function App() {
  const [user, setUser] = useState<any>(null);
  const [sessionChecked, setSessionChecked] = useState(false);
  const [activeTab, setActiveTab] = useState<'inicio' | 'cobros' | 'ventas' | 'flujo'>('inicio');
  
  // Estados para Login
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loginError, setLoginError] = useState('');
  const [loginLoading, setLoginLoading] = useState(false);

  // Estados para datos
  const [kpiData, setKpiData] = useState<KPIResult | null>(null);
  const [pendientesData, setPendientesData] = useState<any>(null);
  const [ventasData, setVentasData] = useState<VentasResult | null>(null);
  const [flujoData, setFlujoData] = useState<FlujoResult | null>(null);
  const [loadingData, setLoadingData] = useState(false);
  const [dataError, setDataError] = useState('');

  // 1. Verificar sesión activa al inicio
  useEffect(() => {
    async function checkSession() {
      try {
        const { data } = await insforge.auth.getCurrentUser();
        if (data?.user) {
          setUser(data.user);
        }
      } catch (err) {
        console.error('Error verificando sesión:', err);
      } finally {
        setSessionChecked(true);
      }
    }
    checkSession();
  }, []);

  // 2. Cargar datos cuando el usuario cambia o se presiona Refresh
  const loadAllData = async () => {
    if (!user) return;
    setLoadingData(true);
    setDataError('');
    try {
      // Cargamos en paralelo para rapidez
      const [kpis, pends, vts, flj] = await Promise.all([
        getKPIs(),
        getPendientes(),
        getVentas(6),
        getFlujo()
      ]);
      setKpiData(kpis);
      setPendientesData(pends);
      setVentasData(vts);
      setFlujoData(flj);
    } catch (err: any) {
      console.error('Error cargando datos de InsForge:', err);
      setDataError(err?.message || 'Error de conexión con la nube. Por favor reintenta.');
    } finally {
      setLoadingData(false);
    }
  };

  useEffect(() => {
    if (user) {
      loadAllData();
    }
  }, [user]);

  // 3. Manejo de Login
  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email || !password) {
      setLoginError('Ingresa correo y contraseña');
      return;
    }
    setLoginError('');
    setLoginLoading(true);
    try {
      const { data, error } = await insforge.auth.signInWithPassword({
        email,
        password
      });
      if (error) {
        setLoginError(error.message || 'Credenciales incorrectas');
      } else if (data?.user) {
        setUser(data.user);
      }
    } catch (err: any) {
      setLoginError(err?.message || 'Error en el inicio de sesión');
    } finally {
      setLoginLoading(false);
    }
  };

  // 4. Manejo de Logout
  const handleLogout = async () => {
    try {
      await insforge.auth.signOut();
      setUser(null);
      // Limpiar estados
      setKpiData(null);
      setPendientesData(null);
      setVentasData(null);
      setFlujoData(null);
    } catch (err) {
      console.error('Error en logout:', err);
    }
  };

  if (!sessionChecked) {
    return (
      <div className="login-container">
        <div className="skeleton-pulse skeleton-card" style={{ width: '100%', maxWidth: '400px', height: '350px' }}></div>
      </div>
    );
  }

  // A. Si no está logueado, mostrar pantalla de Login
  if (!user) {
    return (
      <div className="login-container">
        <main className="glass-panel login-card" id="login-layout">
          <h1 className="login-logo" id="main-title">ZIGURAT ERP</h1>
          <p className="login-subtitle">Centro de Comando Móvil</p>

          {loginError && <div className="error-banner" id="login-error-msg">{loginError}</div>}

          <form onSubmit={handleLogin}>
            <div className="form-group">
              <label className="form-label" htmlFor="user-email">Correo Electrónico</label>
              <input
                id="user-email"
                type="email"
                className="form-input"
                placeholder="correo@ejemplo.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                disabled={loginLoading}
                required
              />
            </div>

            <div className="form-group">
              <label className="form-label" htmlFor="user-password">Contraseña</label>
              <input
                id="user-password"
                type="password"
                className="form-input"
                placeholder="••••••"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={loginLoading}
                required
              />
            </div>

            <button
              id="btn-submit-login"
              type="submit"
              className="btn-primary"
              disabled={loginLoading}
            >
              {loginLoading ? 'Iniciando sesión...' : 'Entrar al Sistema'}
            </button>
          </form>
        </main>
      </div>
    );
  }

  // B. Si está logueado, mostrar Layout Principal
  return (
    <>
      <header className="app-header">
        <h1 className="app-title" id="app-logo">ZIGURAT ERP</h1>
        <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
          <button 
            id="btn-refresh-data"
            onClick={loadAllData} 
            className="btn-logout" 
            style={{ display: 'flex', alignItems: 'center', gap: '4px' }}
            disabled={loadingData}
          >
            <RefreshCw size={14} className={loadingData ? 'spin-anim' : ''} />
          </button>
          <button 
            id="btn-logout-session"
            onClick={handleLogout} 
            className="btn-logout"
            style={{ display: 'flex', alignItems: 'center', gap: '4px' }}
          >
            <LogOut size={14} />
            <span>Salir</span>
          </button>
        </div>
      </header>

      <main className="app-content">
        {dataError && (
          <div className="error-banner" id="global-error-banner" style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
            <AlertTriangle size={18} />
            <span>{dataError}</span>
          </div>
        )}

        {/* Carga simulada con Skeletons */}
        {loadingData && !kpiData ? (
          <div>
            <div className="skeleton-pulse skeleton-title"></div>
            <div className="kpi-grid">
              <div className="skeleton-pulse skeleton-card"></div>
              <div className="skeleton-pulse skeleton-card"></div>
              <div className="skeleton-pulse skeleton-card"></div>
              <div className="skeleton-pulse skeleton-card"></div>
            </div>
          </div>
        ) : (
          <>
            {/* VISTA 1: INICIO (KPIs) */}
            {activeTab === 'inicio' && kpiData && (
              <section id="view-inicio">
                <div className="welcome-section">
                  <h2>¡Hola, Christian!</h2>
                  <p>Panorámica actual de Zigurat Brewery en la nube.</p>
                </div>

                <div className="kpi-grid">
                  <article className="glass-panel kpi-card" id="kpi-ventas-mes">
                    <div className="kpi-header">
                      <span>Ventas del Mes</span>
                      <TrendingUp size={16} color="var(--primary)" />
                    </div>
                    <div className="kpi-value">{formatCLP(kpiData.ventas_mes)}</div>
                    <div className="kpi-footer">
                      <Clock size={12} />
                      <span>Neto real emitido</span>
                    </div>
                  </article>

                  <article className="glass-panel kpi-card" id="kpi-por-cobrar">
                    <div className="kpi-header">
                      <span>Por Cobrar Total</span>
                      <DollarSign size={16} color="var(--primary)" />
                    </div>
                    <div className="kpi-value">{formatCLP(kpiData.por_cobrar)}</div>
                    <div className="kpi-footer">
                      <Layers size={12} />
                      <span>{kpiData.n_pendientes} facturas pendientes</span>
                    </div>
                  </article>

                  <article className="glass-panel kpi-card vencido" id="kpi-vencidas">
                    <div className="kpi-header">
                      <span>Facturas Vencidas</span>
                      <AlertTriangle size={16} color="var(--danger)" />
                    </div>
                    <div className="kpi-value" style={{ color: 'var(--danger)' }}>
                      {formatCLP(kpiData.monto_vencido)}
                    </div>
                    <div className="kpi-footer">
                      <Clock size={12} />
                      <span>{kpiData.n_vencidas} facturas con &gt;30 días</span>
                    </div>
                  </article>

                  <article className="glass-panel kpi-card banco" id="kpi-saldo-banco">
                    <div className="kpi-header">
                      <span>Saldo Itaú (Sync)</span>
                      <DollarSign size={16} color="var(--success)" />
                    </div>
                    <div className="kpi-value" style={{ color: 'var(--success)' }}>
                      {kpiData.saldo_banco ? formatCLP(kpiData.saldo_banco.saldo) : '$0'}
                    </div>
                    <div className="kpi-footer">
                      <Calendar size={12} />
                      <span>
                        Cierre al {kpiData.saldo_banco ? formatDate(kpiData.saldo_banco.fecha) : 'sin registro'}
                      </span>
                    </div>
                  </article>
                </div>

                <div className="glass-panel sync-status-card" id="sync-status">
                  <div className="sync-status-row">
                    <strong>Último Sync Local:</strong>
                    <span className="sync-badge">OK</span>
                  </div>
                  <div className="sync-status-row" style={{ marginTop: '8px' }}>
                    <span>Fecha y hora:</span>
                    <span>{kpiData.ultimo_sync ? formatDateTime(kpiData.ultimo_sync.momento) : 'N/A'}</span>
                  </div>
                  <div className="sync-status-row">
                    <span>Ventas importadas:</span>
                    <span>{kpiData.ultimo_sync?.filas?.ventas ?? 0} registros</span>
                  </div>
                </div>
              </section>
            )}

            {/* VISTA 2: COBRANZA */}
            {activeTab === 'cobros' && (
              <section id="view-cobros">
                <div className="section-header">
                  <h2 className="section-title">Facturas Pendientes</h2>
                  <span className="badge-count" id="cobros-total-count">
                    {pendientesData?.pendientes?.length ?? 0} items
                  </span>
                </div>

                <div className="factura-list" id="list-facturas-pendientes">
                  {pendientesData?.pendientes?.map((f: FacturaPendiente) => {
                    // Determinar nivel de atraso para semáforo
                    let statusClass = 'success';
                    let atrasoText = 'Al día';
                    if (f.dias_desde_emision > 30) {
                      statusClass = 'danger';
                      atrasoText = `Vencido por ${f.dias_desde_emision} días`;
                    } else if (f.dias_desde_emision > 15) {
                      statusClass = 'warning';
                      atrasoText = `Alerta: ${f.dias_desde_emision} días`;
                    } else {
                      atrasoText = `Vence en ${30 - f.dias_desde_emision} días`;
                    }

                    return (
                      <article 
                        key={`${f.folio}-${f.fecha}`} 
                        className={`glass-panel factura-card ${statusClass}`}
                      >
                        <div className="factura-info">
                          <h4>{f.razon_social}</h4>
                          <div className="factura-meta">
                            <span>Folio {f.folio}</span>
                            <span>{formatDate(f.fecha)}</span>
                          </div>
                        </div>
                        <div className="factura-monto">
                          <div className="monto-val">{formatCLP(f.total)}</div>
                          <div className={`dias-atraso ${statusClass}`}>{atrasoText}</div>
                        </div>
                      </article>
                    );
                  })}
                  
                  {(!pendientesData?.pendientes || pendientesData.pendientes.length === 0) && (
                    <div style={{ textAlign: 'center', padding: '30px', color: 'var(--text-muted)' }}>
                      No hay facturas pendientes de cobro.
                    </div>
                  )}
                </div>
              </section>
            )}

            {/* VISTA 3: VENTAS */}
            {activeTab === 'ventas' && ventasData && (
              <section id="view-ventas">
                <div className="section-header">
                  <h2 className="section-title">Ingresos Históricos</h2>
                </div>

                <div className="glass-panel chart-container" id="ventas-monthly-chart">
                  <h3 style={{ fontSize: '0.9rem', color: 'var(--text-muted)', marginBottom: '16px', textTransform: 'uppercase' }}>
                    Ventas Netas 6 Meses
                  </h3>
                  <div style={{ width: '100%', height: 200 }}>
                    <ResponsiveContainer>
                      <BarChart 
                        data={ventasData.serie_mensual.map(s => ({
                          ...s,
                          // Extrae el nombre del mes
                          mesName: new Date(s.mes).toLocaleDateString('es-CL', { month: 'short', timeZone: 'UTC' })
                        }))}
                        margin={{ top: 5, right: 5, left: -25, bottom: 5 }}
                      >
                        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                        <XAxis dataKey="mesName" stroke="var(--text-muted)" fontSize={11} />
                        <YAxis stroke="var(--text-muted)" fontSize={10} tickFormatter={(tick) => formatCLP(tick)} />
                        <Tooltip 
                          formatter={(value: any) => [formatCLP(value), 'Neto']}
                          contentStyle={{ backgroundColor: 'var(--bg-surface-opaque)', borderColor: 'var(--border-glass)', borderRadius: '8px', color: 'var(--text-main)' }}
                        />
                        <Bar dataKey="total" fill="url(#amberGrad)" radius={[4, 4, 0, 0]} />
                        <defs>
                          <linearGradient id="amberGrad" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="0%" stopColor="var(--primary)" />
                            <stop offset="100%" stopColor="var(--accent)" />
                          </linearGradient>
                        </defs>
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                </div>

                <div className="ranking-section">
                  <h3 style={{ fontSize: '1rem', fontWeight: 700, marginBottom: '12px' }}>
                    Top Clientes (Ventas Neto)
                  </h3>
                  <div className="ranking-list" id="list-ranking-clientes">
                    {ventasData.ranking_clientes.map((c, i) => (
                      <article key={c.rut_cliente} className="ranking-item">
                        <div className="ranking-label">
                          <span className="ranking-num">#{i+1}</span>
                          <span className="ranking-name">{c.razon_social}</span>
                        </div>
                        <span className="ranking-value">{formatCLP(c.total)}</span>
                      </article>
                    ))}
                  </div>
                </div>

                <div className="ranking-section" style={{ marginTop: '20px' }}>
                  <h3 style={{ fontSize: '1rem', fontWeight: 700, marginBottom: '12px' }}>
                    Top Cervezas Vendidas (Unidades)
                  </h3>
                  <div className="ranking-list" id="list-ranking-productos">
                    {ventasData.ranking_productos.map((p, i) => (
                      <article key={p.nombre_producto} className="ranking-item">
                        <div className="ranking-label">
                          <span className="ranking-num">#{i+1}</span>
                          <span className="ranking-name" title={p.nombre_producto}>{p.nombre_producto}</span>
                        </div>
                        <span className="ranking-value" style={{ fontWeight: 700, color: 'var(--primary)' }}>
                          {Number(p.unidades).toFixed(0)} barriles
                        </span>
                      </article>
                    ))}
                  </div>
                </div>
              </section>
            )}

            {/* VISTA 4: FLUJO DE CAJA */}
            {activeTab === 'flujo' && flujoData && (
              <section id="view-flujo">
                <div className="section-header">
                  <h2 className="section-title">Proyección a 4 Semanas</h2>
                </div>

                <div className="flujo-meta-grid">
                  <article className="glass-panel flujo-meta-card" id="flujo-meta-ingresos">
                    <div className="flujo-meta-label">Cobros Proyectados</div>
                    <div className="flujo-meta-value ingreso">{formatCLP(flujoData.total_ingresos)}</div>
                  </article>
                  <article className="glass-panel flujo-meta-card" id="flujo-meta-egresos">
                    <div className="flujo-meta-label">Egresos Programados</div>
                    <div className="flujo-meta-value egreso">{formatCLP(flujoData.total_egresos)}</div>
                  </article>
                </div>

                <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }} id="table-flujo-semanas">
                  {flujoData.semanas.map((s) => (
                    <article 
                      key={s.semana} 
                      className={`glass-panel flujo-row ${s.riesgo ? 'risk' : ''}`}
                    >
                      <div className="flujo-row-header">
                        <span>Semana {s.semana} ({s.label})</span>
                        <span style={{ color: s.riesgo ? 'var(--danger)' : 'var(--text-main)' }}>
                          Saldo: {formatCLP(s.saldo_acumulado)}
                        </span>
                      </div>
                      <div className="flujo-row-details">
                        <div className="flujo-detail-item">
                          <span className="flujo-detail-label">Cobros (+):</span>
                          <span className="flujo-detail-val" style={{ color: 'var(--success)' }}>
                            {formatCLP(s.ingresos)}
                          </span>
                        </div>
                        <div className="flujo-detail-item">
                          <span className="flujo-detail-label">Gastos (-):</span>
                          <span className="flujo-detail-val" style={{ color: 'var(--danger)' }}>
                            {formatCLP(s.egresos)}
                          </span>
                        </div>
                      </div>
                    </article>
                  ))}
                </div>

                {flujoData.fuera_horizonte > 0 && (
                  <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', textAlign: 'center', marginTop: '16px' }} id="flujo-fuera-horizonte">
                    * Hay {formatCLP(flujoData.fuera_horizonte)} de cobros proyectados fuera de las próximas 4 semanas.
                  </div>
                )}
              </section>
            )}
          </>
        )}
      </main>

      {/* BOTTOM NAVIGATION BAR */}
      <nav className="bottom-nav" id="bottom-tabs-nav">
        <button 
          id="tab-btn-inicio"
          onClick={() => setActiveTab('inicio')} 
          className={`nav-item ${activeTab === 'inicio' ? 'active' : ''}`}
        >
          <Home size={20} />
          <span>Inicio</span>
        </button>

        <button 
          id="tab-btn-cobros"
          onClick={() => setActiveTab('cobros')} 
          className={`nav-item ${activeTab === 'cobros' ? 'active' : ''}`}
        >
          <FileText size={20} />
          <span>Cobros</span>
        </button>

        <button 
          id="tab-btn-ventas"
          onClick={() => setActiveTab('ventas')} 
          className={`nav-item ${activeTab === 'ventas' ? 'active' : ''}`}
        >
          <TrendingUp size={20} />
          <span>Ventas</span>
        </button>

        <button 
          id="tab-btn-flujo"
          onClick={() => setActiveTab('flujo')} 
          className={`nav-item ${activeTab === 'flujo' ? 'active' : ''}`}
        >
          <DollarSign size={20} />
          <span>Flujo</span>
        </button>
      </nav>
    </>
  );
}
