import { expect, test } from '@playwright/test';
import {
  findExistingClientContact,
  normalizeClientName,
  normalizeClientUrl,
  sameClient,
} from '../scripts/client_contact_guard.mjs';

test('normaliza cliente por URL mesmo com www, barra e /users', () => {
  expect(normalizeClientUrl('https://www.99freelas.com.br/users/42/?from=inbox')).toBe('99freelas.com.br/user/42');
  expect(normalizeClientUrl('/user/42')).toBe('99freelas.com.br/user/42');
});

test('normaliza nome com acentos e espaços', () => {
  expect(normalizeClientName('  João   da Silva ')).toBe('joao da silva');
});

test('bloqueia contato anterior da mesma pessoa por URL ou nome', () => {
  const target = { client: 'João da Silva', client_url: '/users/42' };
  expect(sameClient(target, { client: 'Outro nome', client_url: 'https://99freelas.com.br/user/42' })).toBe(true);
  expect(sameClient(target, { client: '  JOAO DA SILVA ', client_url: '' })).toBe(true);
  expect(sameClient(target, { client: 'João da Silva', client_url: '/users/43' })).toBe(false);
  expect(sameClient(target, { client: 'Maria da Silva', client_url: '/users/43' })).toBe(false);
});

test('encontra cliente já presente em qualquer página da caixa', () => {
  const result = findExistingClientContact([
    { client: 'Outro cliente', client_url: '/users/1' },
    { client: 'João da Silva', client_url: '/users/42' },
  ], { client: 'João da Silva', client_url: '/users/42' });
  expect(result.found).toBe(true);
  expect(result.matches).toHaveLength(1);
});
