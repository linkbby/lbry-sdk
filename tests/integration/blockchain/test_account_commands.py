from binascii import unhexlify

from lbry.testcase import CommandTestCase
from lbry.wallet.dewies import dewies_to_lbc
from lbry.wallet.account import DeterministicChannelKeyManager
from lbry.wallet.transaction import Transaction


def extract(d, keys):
    return {k: d[k] for k in keys}


class AccountManagement(CommandTestCase):
    async def test_account_list_set_create_remove_add(self):
        # check initial account
        accounts = await self.daemon.jsonrpc_account_list()
        self.assertItemCount(accounts, 1)

        # change account name and gap
        account_id = accounts['items'][0]['id']
        self.daemon.jsonrpc_account_set(
            account_id=account_id, new_name='test account',
            receiving_gap=95, receiving_max_uses=96,
            change_gap=97, change_max_uses=98
        )
        accounts = (await self.daemon.jsonrpc_account_list())['items'][0]
        self.assertEqual(accounts['name'], 'test account')
        self.assertEqual(
            accounts['address_generator']['receiving'],
            {'gap': 95, 'maximum_uses_per_address': 96}
        )
        self.assertEqual(
            accounts['address_generator']['change'],
            {'gap': 97, 'maximum_uses_per_address': 98}
        )

        # create another account
        await self.daemon.jsonrpc_account_create('second account')
        accounts = await self.daemon.jsonrpc_account_list()
        self.assertItemCount(accounts, 2)
        self.assertEqual(accounts['items'][1]['name'], 'second account')
        account_id2 = accounts['items'][1]['id']

        # make new account the default
        self.daemon.jsonrpc_account_set(account_id=account_id2, default=True)
        accounts = await self.daemon.jsonrpc_account_list(show_seed=True)
        self.assertEqual(accounts['items'][0]['name'], 'second account')

        account_seed = accounts['items'][1]['seed']

        # remove account
        self.daemon.jsonrpc_account_remove(accounts['items'][1]['id'])
        accounts = await self.daemon.jsonrpc_account_list()
        self.assertItemCount(accounts, 1)

        # add account
        await self.daemon.jsonrpc_account_add('recreated account', seed=account_seed)
        accounts = await self.daemon.jsonrpc_account_list()
        self.assertItemCount(accounts, 2)
        self.assertEqual(accounts['items'][1]['name'], 'recreated account')

        # list specific account
        accounts = await self.daemon.jsonrpc_account_list(account_id, include_claims=True)
        self.assertEqual(accounts['items'][0]['name'], 'recreated account')

    async def assertFindsClaims(self, claim_names, awaitable):
        self.assertEqual(claim_names, [txo.claim_name for txo in (await awaitable)['items']])

    async def assertOutputAmount(self, amounts, awaitable):
        self.assertEqual(amounts, [dewies_to_lbc(txo.amount) for txo in (await awaitable)['items']])

    async def test_commands_across_accounts(self):
        channel_list = self.daemon.jsonrpc_channel_list
        stream_list = self.daemon.jsonrpc_stream_list
        support_list = self.daemon.jsonrpc_support_list
        utxo_list = self.daemon.jsonrpc_utxo_list
        default_account = self.wallet.default_account
        second_account = await self.daemon.jsonrpc_account_create('second account')

        tx = await self.daemon.jsonrpc_account_send(
            '0.05', await self.daemon.jsonrpc_address_unused(account_id=second_account.id)
        )
        await self.confirm_tx(tx.id)
        await self.assertOutputAmount(['0.05', '9.949876'], utxo_list())
        await self.assertOutputAmount(['0.05'], utxo_list(account_id=second_account.id))
        await self.assertOutputAmount(['9.949876'], utxo_list(account_id=default_account.id))

        channel1 = await self.channel_create('@channel-in-account1', '0.01')
        channel2 = await self.channel_create(
            '@channel-in-account2', '0.01', account_id=second_account.id, funding_account_ids=[default_account.id]
        )

        await self.assertFindsClaims(['@channel-in-account2', '@channel-in-account1'], channel_list())
        await self.assertFindsClaims(['@channel-in-account1'], channel_list(account_id=default_account.id))
        await self.assertFindsClaims(['@channel-in-account2'], channel_list(account_id=second_account.id))

        stream1 = await self.stream_create('stream-in-account1', '0.01', channel_id=self.get_claim_id(channel1))
        stream2 = await self.stream_create(
            'stream-in-account2', '0.01', channel_id=self.get_claim_id(channel2),
            account_id=second_account.id, funding_account_ids=[default_account.id]
        )
        await self.assertFindsClaims(['stream-in-account2', 'stream-in-account1'], stream_list())
        await self.assertFindsClaims(['stream-in-account1'], stream_list(account_id=default_account.id))
        await self.assertFindsClaims(['stream-in-account2'], stream_list(account_id=second_account.id))

        await self.assertFindsClaims(
            ['stream-in-account2', 'stream-in-account1', '@channel-in-account2', '@channel-in-account1'],
            self.daemon.jsonrpc_claim_list()
        )
        await self.assertFindsClaims(
            ['stream-in-account1', '@channel-in-account1'],
            self.daemon.jsonrpc_claim_list(account_id=default_account.id)
        )
        await self.assertFindsClaims(
            ['stream-in-account2', '@channel-in-account2'],
            self.daemon.jsonrpc_claim_list(account_id=second_account.id)
        )

        support1 = await self.support_create(self.get_claim_id(stream1), '0.01')
        support2 = await self.support_create(
            self.get_claim_id(stream2), '0.01', account_id=second_account.id, funding_account_ids=[default_account.id]
        )
        self.assertEqual([support2['txid'], support1['txid']], [txo.tx_ref.id for txo in (await support_list())['items']])
        self.assertEqual([support1['txid']], [txo.tx_ref.id for txo in (await support_list(account_id=default_account.id))['items']])
        self.assertEqual([support2['txid']], [txo.tx_ref.id for txo in (await support_list(account_id=second_account.id))['items']])

        history = await self.daemon.jsonrpc_transaction_list()
        self.assertItemCount(history, 8)
        history = history['items']
        self.assertEqual(extract(history[0]['support_info'][0], ['claim_name', 'is_tip', 'amount', 'balance_delta']), {
            'claim_name': 'stream-in-account2',
            'is_tip': False,
            'amount': '0.01',
            'balance_delta': '-0.01'
        })
        self.assertEqual(extract(history[1]['support_info'][0], ['claim_name', 'is_tip', 'amount', 'balance_delta']), {
            'claim_name': 'stream-in-account1',
            'is_tip': False,
            'amount': '0.01',
            'balance_delta': '-0.01'
        })
        self.assertEqual(extract(history[2]['claim_info'][0], ['claim_name', 'amount', 'balance_delta']), {
            'claim_name': 'stream-in-account2',
            'amount': '0.01',
            'balance_delta': '-0.01'
        })
        self.assertEqual(extract(history[3]['claim_info'][0], ['claim_name', 'amount', 'balance_delta']), {
            'claim_name': 'stream-in-account1',
            'amount': '0.01',
            'balance_delta': '-0.01'
        })
        self.assertEqual(extract(history[4]['claim_info'][0], ['claim_name', 'amount', 'balance_delta']), {
            'claim_name': '@channel-in-account2',
            'amount': '0.01',
            'balance_delta': '-0.01'
        })
        self.assertEqual(extract(history[5]['claim_info'][0], ['claim_name', 'amount', 'balance_delta']), {
            'claim_name': '@channel-in-account1',
            'amount': '0.01',
            'balance_delta': '-0.01'
        })
        self.assertEqual(history[6]['value'], '0.0')
        self.assertEqual(history[7]['value'], '10.0')

    async def test_address_validation(self):
        address = await self.daemon.jsonrpc_address_unused()
        bad_address = address[0:20] + '9999999' + address[27:]
        with self.assertRaisesRegex(Exception, f"'{bad_address}' is not a valid address"):
            await self.daemon.jsonrpc_account_send('0.1', addresses=[bad_address])

    async def test_backwards_compatibility(self):
        pk = {
            'mpAt7RQJUWe3RWPyyYQ9cinQoPH9HomPdh':
                '-----BEGIN EC PRIVATE KEY-----\nMHQCAQEEIMrKg13+6mj5zdqN2wCx24GgYD8PUiYVzGewgOvu24SfoA'
                'cGBSuBBAAK\noUQDQgAE1/oT/Y5X86C4eOqvPReRRNJd2+Sj5EQKZh9RtBNMahPJyYZ4/4QRky5g\n/ZfXuvA+'
                'pn68whCXIwz7IkE0iq21Xg==\n-----END EC PRIVATE KEY-----\n'
        }

    async def test_deterministic_channel_keys(self):
        seed = self.account.seed
        keys = self.account.deterministic_channel_keys

        # create two channels and make sure they have different keys
        channel1a = await self.channel_create('@foo1')
        channel2a = await self.channel_create('@foo2')
        self.assertNotEqual(
            channel1a['outputs'][0]['value']['public_key'],
            channel2a['outputs'][0]['value']['public_key'],
        )

        # start another daemon from the same seed
        self.daemon2 = await self.add_daemon(seed=seed)
        channel2b, channel1b = (await self.daemon2.jsonrpc_channel_list())['items']

        # both daemons end up with the same channel signing keys automagically
        self.assertTrue(channel1b.has_private_key)
        self.assertEqual(
            channel1a['outputs'][0]['value']['public_key_id'],
            self.ledger.public_key_to_address(channel1b.private_key.verifying_key.to_der())
        )
        self.assertTrue(channel2b.has_private_key)
        self.assertEqual(
            channel2a['outputs'][0]['value']['public_key_id'],
            self.ledger.public_key_to_address(channel2b.private_key.verifying_key.to_der())
        )

        # repeatedly calling next channel key returns the same key when not used
        current_known = keys.last_known
        next_key = await keys.generate_next_key()
        self.assertEqual(current_known, keys.last_known)
        self.assertEqual(next_key.to_string(), (await keys.generate_next_key()).to_string())
        # again, should be idempotent
        next_key = await keys.generate_next_key()
        self.assertEqual(current_known, keys.last_known)
        self.assertEqual(next_key.to_string(), (await keys.generate_next_key()).to_string())

        # create third channel while both daemons running, second daemon should pick it up
        channel3a = await self.channel_create('@foo3')
        self.assertEqual(current_known+1, keys.last_known)
        self.assertNotEqual(next_key.to_string(), (await keys.generate_next_key()).to_string())
        channel3b, = (await self.daemon2.jsonrpc_channel_list(name='@foo3'))['items']
        self.assertTrue(channel3b.has_private_key)
        self.assertEqual(
            channel3a['outputs'][0]['value']['public_key_id'],
            self.ledger.public_key_to_address(channel3b.private_key.verifying_key.to_der())
        )

        # channel key cache re-populated after simulated restart

        # reset cache
        self.account.deterministic_channel_keys = DeterministicChannelKeyManager(self.account)
        channel3c, channel2c, channel1c = (await self.daemon.jsonrpc_channel_list())['items']
        self.assertFalse(channel1c.has_private_key)
        self.assertFalse(channel2c.has_private_key)
        self.assertFalse(channel3c.has_private_key)

        # repopulate cache
        await self.account.deterministic_channel_keys.ensure_cache_primed()
        self.assertEqual(self.account.deterministic_channel_keys.last_known, keys.last_known)
        channel3c, channel2c, channel1c = (await self.daemon.jsonrpc_channel_list())['items']
        self.assertTrue(channel1c.has_private_key)
        self.assertTrue(channel2c.has_private_key)
        self.assertTrue(channel3c.has_private_key)

