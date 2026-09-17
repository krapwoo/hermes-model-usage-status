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

const DATA_CONTRACT_VERSION = 3
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

function percent(value) {
  return Number.isFinite(value) ? `${Math.round(value)}%` : '—'
}

function compactLabel(providerId, provider) {
  const name = providerId === 'claude' ? 'Claude' : 'Codex'
  const windows = Array.isArray(provider?.windows) ? provider.windows : []
  if (!windows.length) {
    return `${name} —`
  }
  const values = windows.map(window => `${window.label} ${percent(window.remaining_percent)}`).join(' · ')
  const stale = provider.status === 'stale' || provider.status === 'expired' ? ' · stale' : ''
  return `${name} ${values}${stale}`
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
      const next = await call('/refresh', { method: 'POST' })
      queryClient.setQueryData(QUERY_KEY, next)
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
