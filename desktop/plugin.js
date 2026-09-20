import {
  cn,
  Codicon,
  DropdownMenuItem,
  queryClient,
  STATUSBAR_AREAS,
  useQuery
} from '@hermes/plugin-sdk'
import { useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const DATA_CONTRACT_VERSION = 4
const QUERY_KEY = ['model-usage-status', DATA_CONTRACT_VERSION]
let rest = null

function bindRest(value) {
  rest = value
  return () => {
    rest = null
  }
}

function call(path, options) {
  return rest ? rest(path, options) : Promise.reject(new Error('model usage API unavailable'))
}

function useUsage() {
  return useQuery({
    queryFn: () => call('/usage'),
    queryKey: QUERY_KEY,
    refetchInterval: 300_000,
    staleTime: 240_000
  })
}

// A failed refresh must keep whatever safe query data is already cached and
// must never let its rejection reach the caller unhandled, so the request and
// its outcome are isolated here: apply the result only on success, and always
// resolve.
async function refreshUsage(requestRefresh) {
  try {
    const next = await requestRefresh()
    queryClient.setQueryData(QUERY_KEY, next)
    return { ok: true }
  } catch (_error) {
    return { ok: false }
  }
}

function percent(value) {
  return Number.isFinite(value) ? `${Math.round(value)}%` : '—'
}

function formatCurrencyMinor(amountMinor, currency, scale, locale) {
  if (!Number.isSafeInteger(amountMinor) || amountMinor < 0 ||
      !/^[A-Z]{3}$/.test(currency || '') || !Number.isInteger(scale) || scale < 0 || scale > 6) {
    return null
  }
  const divisor = 10 ** scale
  const fractionDigits = amountMinor % divisor === 0 ? 0 : scale
  return new Intl.NumberFormat(locale ? [locale] : [], {
    style: 'currency', currency,
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits
  }).format(amountMinor / divisor)
}

function selectCompactAllowance(windows) {
  const valid = (Array.isArray(windows) ? windows : []).filter(
    window => Number.isFinite(window?.remaining_percent) &&
      (window.duration_minutes === 300 || window.duration_minutes === 10080)
  )
  const weekly = valid.find(window => window.duration_minutes === 10080)
  const short = valid.find(window => window.duration_minutes === 300)
  if (weekly?.remaining_percent === 0) return weekly
  if (short?.remaining_percent === 0) return short
  if (!short) return weekly || null
  if (!weekly) return short
  return weekly.remaining_percent <= short.remaining_percent ? weekly : short
}

function creditIsConsequential(credits) {
  return credits?.status === 'current' &&
    Boolean(credits.active || credits.low || credits.exhausted)
}

const UNAVAILABLE_CREDIT_PRESENTATION = {
  primary: 'Unavailable', supporting: null, monthly: null, compact: null, consequential: false
}
const CLAUDE_OFF_SUPPORT = 'Paid extra usage is not enabled.'
const CODEX_OFF_SUPPORT = 'Credit-backed usage is not available'
const CODEX_HIDDEN_BALANCE_SUPPORT = 'The provider did not return a balance'

function creditPresentation(providerId, credits, locale) {
  if (!credits || typeof credits !== 'object') return UNAVAILABLE_CREDIT_PRESENTATION
  const stale = credits.freshness === 'stale'

  if (credits.status === 'off') {
    return {
      primary: stale ? 'Off · stale' : 'Off',
      supporting: providerId === 'claude' ? CLAUDE_OFF_SUPPORT : CODEX_OFF_SUPPORT,
      monthly: null, compact: null, consequential: false
    }
  }
  if (credits.status !== 'current') return UNAVAILABLE_CREDIT_PRESENTATION

  const consequential = creditIsConsequential(credits)

  if (credits.unit === 'currency') {
    const { currency, minor_unit_scale: scale, spend } = credits
    if (!spend || !Number.isSafeInteger(spend.used_minor) || !Number.isSafeInteger(spend.limit_minor)) {
      return UNAVAILABLE_CREDIT_PRESENTATION
    }
    const remainingMinor = Math.max(spend.limit_minor - spend.used_minor, 0)
    const used = formatCurrencyMinor(spend.used_minor, currency, scale, locale)
    const limit = formatCurrencyMinor(spend.limit_minor, currency, scale, locale)
    const remaining = formatCurrencyMinor(remainingMinor, currency, scale, locale)
    if (used === null || limit === null || remaining === null) return UNAVAILABLE_CREDIT_PRESENTATION

    const qualifier = credits.exhausted ? 'Exhausted' : credits.low ? 'Low' : null
    const primaryBase = `${used} of ${limit} used`
    const primaryText = qualifier ? `${primaryBase} · ${qualifier}` : primaryBase
    const compactBase = `Credits ${used}/${limit}`
    const compact = qualifier ? `${compactBase} · ${qualifier}` : compactBase

    return {
      primary: stale ? `${primaryText} · stale` : primaryText,
      supporting: `${remaining} remains this month`,
      monthly: null,
      compact,
      consequential
    }
  }

  if (credits.unit === 'credits') {
    const { balance, spend } = credits
    let base = null
    let supporting = null
    if (balance && balance.unlimited) {
      base = 'Unlimited'
    } else if (balance && balance.amount_credits != null) {
      base = `${balance.amount_credits} credits left`
    } else if (balance && balance.available) {
      base = 'Available'
      supporting = CODEX_HIDDEN_BALANCE_SUPPORT
    }

    // A numeric zero balance is independently exhausted; the monthly-limit wording
    // is reserved for when the qualifier actually derives from `spend` while a
    // nonzero/hidden/unlimited balance is shown, or when no balance exists at all.
    const isZeroBalance = Boolean(balance) && balance.amount_credits != null &&
      Number(balance.amount_credits) === 0
    const monthlyQualified = isZeroBalance ? false : (!base || Boolean(spend))
    const qualifier = credits.exhausted
      ? (monthlyQualified ? 'Monthly limit reached' : 'Exhausted')
      : credits.low
        ? (monthlyQualified ? 'Monthly limit low' : 'Low')
        : null

    let text
    if (base && qualifier) {
      text = `${base} · ${qualifier}`
    } else if (base) {
      text = base
    } else if (qualifier) {
      text = qualifier
    } else {
      return UNAVAILABLE_CREDIT_PRESENTATION
    }

    // The monthly row always truthfully reports the spend object's own state,
    // independent of which wording the primary/compact qualifier used above.
    const monthly = spend ? {
      used: spend.used_credits,
      limit: spend.limit_credits,
      remainingPercent: spend.remaining_percent,
      resetsAt: spend.resets_at,
      reached: Number.isFinite(spend.remaining_percent) && spend.remaining_percent <= 0
    } : null

    return {
      primary: stale ? `${text} · stale` : text,
      supporting,
      monthly,
      compact: text,
      consequential
    }
  }

  return UNAVAILABLE_CREDIT_PRESENTATION
}

function compactLabel(providerId, provider, locale) {
  const name = providerId === 'claude' ? 'Claude' : 'Codex'
  const windows = Array.isArray(provider?.windows) ? provider.windows : []
  const credits = provider?.credits
  const presentation = creditPresentation(providerId, credits, locale)

  if (!presentation.consequential || !presentation.compact) {
    if (!windows.length) {
      return `${name} —`
    }
    const values = windows.map(window => `${window.label} ${percent(window.remaining_percent)}`).join(' · ')
    const stale = provider.status === 'stale' || provider.status === 'expired' ? ' · stale' : ''
    return `${name} ${values}${stale}`
  }

  const allowance = selectCompactAllowance(windows)
  const allowanceFact = allowance ? `${allowance.label} ${percent(allowance.remaining_percent)}` : null
  const parts = [allowanceFact, presentation.compact].filter(Boolean)
  const stale = provider?.status === 'stale' || provider?.status === 'expired' || credits?.freshness === 'stale'
    ? ' · stale'
    : ''
  return `${name} ${parts.join(' · ')}${stale}`
}

function dateTime(epoch) {
  if (!Number.isFinite(epoch)) {
    return 'Reset unavailable'
  }
  return new Date(epoch * 1000).toLocaleString([], {
    dateStyle: 'medium',
    timeStyle: 'short'
  })
}

function age(epoch) {
  if (!Number.isFinite(epoch)) {
    return 'Never observed'
  }
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - epoch))
  if (seconds < 60) return 'Observed just now'
  if (seconds < 3600) return `Observed ${Math.floor(seconds / 60)}m ago`
  if (seconds < 86400) return `Observed ${Math.floor(seconds / 3600)}h ago`
  return `Observed ${Math.floor(seconds / 86400)}d ago`
}

function errorText(provider) {
  if (provider?.status === 'current') return null
  const messages = {
    authentication_required: 'Claude rejected the saved authentication. Sign in again to refresh usage.',
    observation_stale: 'Claude has not supplied a newer structured observation yet.',
    observation_unavailable: 'Use Claude Code normally once to populate its provider-native meters.',
    provider_unavailable: 'The provider usage surface could not be refreshed.',
    refresh_unavailable: 'A refresh is already unavailable.',
    expired: 'The previous observation passed its reset time and was discarded.'
  }
  return messages[provider?.error_code] || provider?.status || 'unavailable'
}

function WindowRow({ window }) {
  return jsxs('div', {
    className: 'flex items-start justify-between gap-4 py-1',
    children: [
      jsxs('div', {
        className: 'min-w-0',
        children: [
          jsx('div', { className: 'font-medium text-foreground', children: window.label }),
          jsx('div', {
            className: 'text-[0.6875rem] text-(--ui-text-tertiary)',
            children: dateTime(window.resets_at)
          })
        ]
      }),
      jsx('div', {
        className: 'shrink-0 tabular-nums text-foreground',
        children: `${percent(window.remaining_percent)} left`
      })
    ]
  })
}

function CreditsSection({ providerId, credits }) {
  const presentation = creditPresentation(providerId, credits)
  const stale = credits?.freshness === 'stale'
  const monthly = presentation.monthly

  return jsxs('div', {
    className: 'border-t border-(--ui-stroke-secondary) pt-2',
    children: [
      jsx('div', { className: 'pb-1 font-medium text-(--ui-text-secondary)', children: 'Credits' }),
      jsxs('div', {
        className: 'space-y-2',
        children: [
          jsx('div', { className: 'text-foreground', children: presentation.primary }),
          presentation.supporting
            ? jsx('div', {
                className: 'text-[0.6875rem] text-(--ui-text-tertiary)',
                children: presentation.supporting
              })
            : null,
          stale
            ? jsx('div', {
                className: 'text-[0.6875rem] text-(--ui-text-tertiary)',
                children: age(credits.observed_at)
              })
            : null
        ]
      }),
      monthly
        ? jsxs('div', {
            className: 'border-t border-(--ui-stroke-secondary) pt-2',
            children: [
              jsx('div', {
                className: 'pb-1 font-medium text-(--ui-text-secondary)',
                children: 'Monthly credit limit'
              }),
              jsx('div', {
                className: 'text-foreground',
                children: monthly.reached
                  ? `${monthly.used}/${monthly.limit} credits · Reached`
                  : `${monthly.used}/${monthly.limit} credits · ${percent(monthly.remainingPercent)} remaining`
              }),
              jsx('div', {
                className: 'text-[0.6875rem] text-(--ui-text-tertiary)',
                children: dateTime(monthly.resetsAt)
              })
            ]
          })
        : null
    ]
  })
}

function ProviderDetails({
  providerId,
  provider,
  generatedAt,
  onReauthenticate,
  onRefresh,
  reauthenticating,
  reauthenticationMessage,
  refreshing
}) {
  const name = providerId === 'claude' ? 'Claude' : 'Codex'
  const windows = Array.isArray(provider?.windows) ? provider.windows : []
  const modelLimits = Array.isArray(provider?.model_limits) ? provider.model_limits : []
  const issue = errorText(provider)

  return jsxs('div', {
    className: 'w-[20rem] space-y-3 p-3 text-xs',
    children: [
      jsxs('div', {
        className: 'flex items-center justify-between gap-3',
        children: [
          jsxs('div', {
            children: [
              jsx('div', { className: 'font-medium text-foreground', children: `${name} usage` }),
              jsx('div', {
                className: 'text-[0.6875rem] text-(--ui-text-tertiary)',
                children: age(provider?.observed_at || generatedAt)
              })
            ]
          }),
          jsxs(DropdownMenuItem, {
            'aria-label': `Refresh ${name} usage`,
            className: cn(
              'inline-flex items-center gap-1 rounded px-2 py-1 transition-colors',
              'text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground',
              'disabled:cursor-wait disabled:opacity-60'
            ),
            disabled: refreshing,
            onSelect: event => {
              event.preventDefault()
              void onRefresh()
            },
            children: [
              jsx(Codicon, { name: refreshing ? 'loading' : 'refresh', size: '0.75rem' }),
              refreshing ? 'Refreshing' : 'Refresh'
            ]
          })
        ]
      }),
      issue
        ? jsx('div', {
            className: 'rounded border border-(--ui-stroke-secondary) p-2 text-(--ui-text-secondary)',
            children: issue
          })
        : null,
      provider?.authentication?.state === 'required'
        ? jsxs('div', {
            className: 'space-y-2 rounded border border-(--ui-stroke-secondary) p-2',
            children: [
              jsx('div', {
                className: 'text-(--ui-text-secondary)',
                children: `${name} authentication is required.`
              }),
              jsxs(DropdownMenuItem, {
                'aria-label': `Reauthenticate ${name}`,
                className: cn(
                  'inline-flex items-center gap-1 rounded border border-(--ui-stroke-secondary) px-2 py-1',
                  'text-foreground hover:bg-(--chrome-action-hover)',
                  'disabled:cursor-wait disabled:opacity-60'
                ),
                disabled: reauthenticating,
                onSelect: event => {
                  event.preventDefault()
                  void onReauthenticate()
                },
                children: reauthenticating ? 'Opening Terminal…' : `Reauthenticate ${name}`
              }),
              reauthenticationMessage
                ? jsx('div', {
                    className: 'text-[0.6875rem] text-(--ui-text-tertiary)',
                    children: reauthenticationMessage
                  })
                : null
            ]
          })
        : null,
      windows.length
        ? jsx('div', {
            className: 'divide-y divide-(--ui-stroke-secondary)',
            children: windows.map(window => jsx(WindowRow, { window }, window.id))
          })
        : jsx('div', {
            className: 'py-2 text-(--ui-text-tertiary)',
            children: `${name} usage is unavailable.`
          }),
      modelLimits.map(model =>
        jsxs('div', {
          className: 'border-t border-(--ui-stroke-secondary) pt-2',
          children: [
            jsx('div', {
              className: 'pb-1 font-medium text-(--ui-text-secondary)',
              children: model.label
            }),
            jsx('div', {
              className: 'divide-y divide-(--ui-stroke-secondary)',
              children: model.windows.map(window => jsx(WindowRow, { window }, window.id))
            })
          ]
        }, model.id)
      ),
      jsx(CreditsSection, { providerId, credits: provider?.credits }),
      provider?.source
        ? jsx('div', {
            className: 'border-t border-(--ui-stroke-secondary) pt-2 text-[0.625rem] text-(--ui-text-quaternary)',
            children: provider.source
          })
        : null
    ]
  })
}

function ProviderLabel({ providerId }) {
  const { data, isLoading } = useUsage()
  const provider = data?.providers?.[providerId]

  return isLoading ? `${providerId === 'claude' ? 'Claude' : 'Codex'} …` : compactLabel(providerId, provider)
}

function ProviderMenu({ providerId }) {
  const { data, isLoading } = useUsage()
  const [refreshing, setRefreshing] = useState(false)
  const [reauthenticating, setReauthenticating] = useState(false)
  const [reauthenticationMessage, setReauthenticationMessage] = useState(null)
  const provider = data?.providers?.[providerId]

  const refresh = async () => {
    if (refreshing) return
    setRefreshing(true)
    try {
      await refreshUsage(() => call('/refresh', { method: 'POST' }))
    } finally {
      setRefreshing(false)
    }
  }

  const reauthenticate = async () => {
    if (reauthenticating) return
    setReauthenticating(true)
    setReauthenticationMessage(null)
    try {
      await call(`/authentication/${providerId}`, { method: 'POST' })
      queryClient.invalidateQueries({ queryKey: QUERY_KEY })
      setReauthenticationMessage('Continue in Terminal, then select Refresh.')
    } catch (_error) {
      setReauthenticationMessage('Could not open the provider login. Try again or use the provider CLI.')
    } finally {
      setReauthenticating(false)
    }
  }

  return jsx(ProviderDetails, {
    generatedAt: data?.generated_at,
    onReauthenticate: reauthenticate,
    onRefresh: refresh,
    provider: isLoading ? null : provider,
    providerId,
    reauthenticating,
    reauthenticationMessage,
    refreshing
  })
}

function statusItem({ id, providerId, toggleLabel }) {
  return {
    id,
    label: jsx(ProviderLabel, { providerId }),
    menuAlign: 'end',
    menuClassName: 'w-auto p-0',
    menuContent: jsx(ProviderMenu, { providerId }),
    title: toggleLabel,
    toggleLabel,
    variant: 'menu'
  }
}

export default {
  id: 'model-usage-status',
  name: 'Model Usage Status',
  description: 'Provider-native Claude and Codex remaining usage in the Hermes status bar.',
  register(ctx) {
    ctx.onDispose(bindRest(ctx.rest))
    ctx.register({
      id: 'claude',
      area: STATUSBAR_AREAS.right,
      order: 128,
      data: statusItem({
        id: 'model-usage-status:claude',
        providerId: 'claude',
        toggleLabel: 'Claude model usage'
      })
    })
    ctx.register({
      id: 'codex',
      area: STATUSBAR_AREAS.right,
      order: 129,
      data: statusItem({
        id: 'model-usage-status:codex',
        providerId: 'codex',
        toggleLabel: 'Codex model usage'
      })
    })
  }
}

export { CreditsSection, compactLabel, creditPresentation, formatCurrencyMinor, refreshUsage, selectCompactAllowance }
