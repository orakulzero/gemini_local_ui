import { describe, it, expect, beforeEach, vi } from 'vitest';
import { useAccountsStore } from '../store/accountsStore';
import * as api from '../services/api';

vi.mock('../services/api');

describe('useAccountsStore', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAccountsStore.setState({
      accounts: [],
      activeAccountId: 'default',
      failoverConfig: null,
      limitsData: null,
      isLimitsModalOpen: false,
      isAddAccountOpen: false,
      isLoading: false,
      error: null,
    });
  });

  it('initializes with default state', () => {
    const state = useAccountsStore.getState();
    expect(state.activeAccountId).toBe('default');
    expect(state.accounts).toEqual([]);
    expect(state.failoverConfig).toBeNull();
    expect(state.isLimitsModalOpen).toBe(false);
    expect(state.isAddAccountOpen).toBe(false);
  });

  it('fetchAccounts populates accounts and failover config', async () => {
    const mockAccounts = [
      {
        id: 'acc_pro',
        name: 'Pro Account',
        tier: 'PRO',
        is_active: true,
        priority: 1,
        health: {
          status: 'healthy',
          consecutive_failures: 0,
          error_1037_cooldown_until: 0,
        },
      },
      {
        id: 'acc_ultra',
        name: 'Ultra Account',
        tier: 'ULTRA',
        is_active: false,
        priority: 2,
        health: {
          status: 'healthy',
          consecutive_failures: 0,
          error_1037_cooldown_until: 0,
        },
      },
    ];
    const mockFailover = {
      enabled: true,
      strategy: 'priority',
      cooldown_duration_seconds: 300,
      fallback_chain: ['acc_pro', 'acc_ultra'],
      notify_client_on_failover: true,
    };

    vi.mocked(api.fetchAccounts).mockResolvedValueOnce({
      accounts: mockAccounts as any,
      active_account_id: 'acc_pro',
      failover_config: mockFailover as any,
    });

    await useAccountsStore.getState().fetchAccounts();

    const state = useAccountsStore.getState();
    expect(state.accounts.length).toBe(2);
    expect(state.activeAccountId).toBe('acc_pro');
    expect(state.failoverConfig?.enabled).toBe(true);
    expect(state.isLoading).toBe(false);
    expect(state.error).toBeNull();
  });

  it('activateAccount switches active account and refetches', async () => {
    vi.mocked(api.activateAccount).mockResolvedValueOnce(true);
    vi.mocked(api.fetchAccounts).mockResolvedValueOnce({
      accounts: [
        { id: 'acc_ultra', name: 'Ultra Account', tier: 'ULTRA', is_active: true } as any,
      ],
      active_account_id: 'acc_ultra',
      failover_config: {
        enabled: true,
        strategy: 'priority',
        cooldown_duration_seconds: 300,
        fallback_chain: ['acc_ultra'],
        notify_client_on_failover: true,
      } as any,
    });

    const success = await useAccountsStore.getState().activateAccount('acc_ultra');

    expect(success).toBe(true);
    expect(api.activateAccount).toHaveBeenCalledWith('acc_ultra');
    expect(useAccountsStore.getState().activeAccountId).toBe('acc_ultra');
  });

  it('toggleFailover updates local state and calls api', async () => {
    useAccountsStore.setState({
      failoverConfig: {
        enabled: false,
        strategy: 'priority',
        cooldown_duration_seconds: 300,
        fallback_chain: ['acc_pro'],
        notify_client_on_failover: true,
      },
    });

    vi.mocked(api.updateFailoverConfig).mockResolvedValueOnce({
      enabled: true,
      strategy: 'priority',
      cooldown_duration_seconds: 300,
      fallback_chain: ['acc_pro'],
      notify_client_on_failover: true,
    } as any);

    await useAccountsStore.getState().toggleFailover(true);

    expect(api.updateFailoverConfig).toHaveBeenCalledWith({ enabled: true });
    expect(useAccountsStore.getState().failoverConfig?.enabled).toBe(true);
  });

  it('toggleLimitsModal and toggleAddAccount switch boolean states', () => {
    const store = useAccountsStore.getState();

    store.toggleLimitsModal(true);
    expect(useAccountsStore.getState().isLimitsModalOpen).toBe(true);

    store.toggleLimitsModal();
    expect(useAccountsStore.getState().isLimitsModalOpen).toBe(false);

    store.toggleAddAccount(true);
    expect(useAccountsStore.getState().isAddAccountOpen).toBe(true);

    store.toggleAddAccount(false);
    expect(useAccountsStore.getState().isAddAccountOpen).toBe(false);
  });

  it('handles fetchAccounts error gracefully', async () => {
    vi.mocked(api.fetchAccounts).mockRejectedValueOnce(new Error('Network error'));

    await useAccountsStore.getState().fetchAccounts();

    const state = useAccountsStore.getState();
    expect(state.isLoading).toBe(false);
    expect(state.error).toBe('Network error');
  });
});
