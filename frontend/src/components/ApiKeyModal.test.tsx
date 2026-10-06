import { useState } from 'react';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError } from '../api';
import { ApiKeyItem } from '../types';
import { ApiKeyModal } from './ApiKeyModal';

const secret = 'sk-test-only-plain-secret';
const item: ApiKeyItem = {
  provider: 'openai', name: 'OpenAI', has_key: true, masked_key: 'sk-t••••••••cret',
  source: 'stored', placeholder: 'sk-proj-…', detected_env_var: null, status: 'connected',
  last_verified: null, is_admin_key: false,
};

beforeEach(() => {
  vi.spyOn(api, 'listApiKeys').mockResolvedValue([item]);
  vi.spyOn(api, 'providerSpend').mockResolvedValue({});
  vi.spyOn(api, 'setProviderKey').mockResolvedValue({ verification: { valid: true } });
  vi.spyOn(api, 'verifyProviderKey').mockResolvedValue({ valid: true });
  vi.spyOn(api, 'deleteProviderKey').mockResolvedValue(undefined);
});

function renderModal() {
  const props = { isOpen: true, onClose: vi.fn(), onKeysChanged: vi.fn(), onOpenSettings: vi.fn() };
  render(<ApiKeyModal {...props} />);
  return props;
}

describe('provider key management', () => {
  it('shows only the masked key and unavailable billing', async () => {
    const view = renderModal();
    expect(await screen.findByText(item.masked_key)).toBeInTheDocument();
    expect(screen.getByText('공식 청구/잔액: 확인 불가')).toBeInTheDocument();
    expect(screen.getByLabelText('OpenAI API 키 교체')).toHaveAttribute('type', 'password');
    expect(screen.getByLabelText('OpenAI API 키 교체')).toHaveValue('');
    expect(view.onKeysChanged).not.toHaveBeenCalled();
  });

  it('saves a password, clears it and refreshes without displaying plaintext', async () => {
    const user = userEvent.setup();
    const props = renderModal();
    const input = await screen.findByLabelText('OpenAI API 키 교체');
    await user.type(input, secret);
    await user.click(screen.getByRole('button', { name: '저장' }));
    await waitFor(() => expect(api.setProviderKey).toHaveBeenCalledWith('openai', secret));
    await waitFor(() => expect(input).toHaveValue(''));
    expect(props.onKeysChanged).toHaveBeenCalledOnce();
    expect(document.body.textContent).not.toContain(secret);
    expect(document.body.innerHTML).not.toContain(secret);
  });

  it.each([401, 403])('guides rejected writes (%s) to server settings without echoing an error secret', async (status) => {
    vi.mocked(api.setProviderKey).mockRejectedValue(new ApiError(secret, status));
    const user = userEvent.setup();
    const props = renderModal();
    await user.type(await screen.findByLabelText('OpenAI API 키 교체'), secret);
    await user.click(screen.getByRole('button', { name: '저장' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('쓰기 권한이 있는 서버 API 키');
    expect(screen.getByRole('alert')).not.toHaveTextContent(secret);
    await user.click(screen.getByRole('button', { name: 'API 키 입력' }));
    expect(props.onOpenSettings).toHaveBeenCalledOnce();
    expect(props.onKeysChanged).not.toHaveBeenCalled();
  });

  it('suppresses unexpected error details and invalid unmasked server values', async () => {
    vi.mocked(api.listApiKeys).mockResolvedValue([{ ...item, masked_key: secret }]);
    vi.mocked(api.verifyProviderKey).mockRejectedValue(new Error(secret));
    const user = userEvent.setup();
    renderModal();
    await user.click(await screen.findByRole('button', { name: '검증' }));
    expect(await screen.findByRole('alert')).not.toHaveTextContent(secret);
    expect(document.body.textContent).not.toContain(secret);
  });

  it('verifies and deletes stored keys; environment keys cannot be deleted', async () => {
    const user = userEvent.setup();
    renderModal();
    await user.click(await screen.findByRole('button', { name: '검증' }));
    expect(await screen.findByText('공급자 검증 성공')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: '삭제' })).toBeEnabled());
    vi.mocked(api.listApiKeys).mockResolvedValue([{ ...item, source: 'env' }]);
    await user.click(screen.getByRole('button', { name: '삭제' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: '삭제' })).not.toBeInTheDocument());
    expect(api.deleteProviderKey).toHaveBeenCalledWith('openai');
  });

  it('traps keyboard focus, clears input on closing and restores the opener', async () => {
    const user = userEvent.setup();
    function Harness() {
      const [open, setOpen] = useState(false);
      return <><button onClick={() => setOpen(true)}>키 관리 열기</button>
        <ApiKeyModal isOpen={open} onClose={() => setOpen(false)} onKeysChanged={vi.fn()} onOpenSettings={vi.fn()} /></>;
    }
    render(<Harness />);
    const opener = screen.getByRole('button', { name: '키 관리 열기' });
    await user.click(opener);
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByRole('button', { name: '닫기' })).toHaveFocus();
    await user.type(await screen.findByLabelText('OpenAI API 키 교체'), secret);
    await user.keyboard('{Escape}');
    expect(opener).toHaveFocus();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await user.click(opener);
    expect(await screen.findByLabelText('OpenAI API 키 교체')).toHaveValue('');
  });
});
