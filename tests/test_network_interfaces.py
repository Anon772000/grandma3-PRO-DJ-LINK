from pathlib import Path
import unittest

from lupa.lua54 import LuaRuntime


ROOT = Path(__file__).resolve().parents[1]


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.net = self.lua.execute((ROOT / 'src/cdj-link-network.lua').read_text())
        self.lua.globals().Network = self.net
        self.lua.execute('''
            function node(properties, children)
                properties.Children = function() return children or {} end
                return properties
            end
            cards = {}
            Root = function() return {Interfaces=node({}, cards)} end
            function card(name, mac, link, ip, mask)
                return node({Name=name, MAC=mac, Link=link}, {node({IP=ip, Mask=mask})})
            end
            function entries() local result=Network.list(Root); return result end
        ''')

    def table(self, code):
        return self.lua.eval(code)

    def entries(self):
        return self.lua.globals().entries()

    def test_broadcast_uses_actual_mask(self):
        for ip, mask, expected, prefix in (
            ('169.254.50.10', '255.255.0.0', '169.254.255.255', 16),
            ('192.168.50.10', '255.255.255.0', '192.168.50.255', 24),
            ('10.20.4.10', '255.255.254.0', '10.20.5.255', 23),
            ('172.20.1.10', '255.240.0.0', '172.31.255.255', 12),
        ):
            self.assertEqual(self.net.broadcast(ip, mask), (expected, prefix))

    def test_invalid_addresses_masks_and_broadcastless_networks(self):
        for ip in ('1..2.3.4', '.1.2.3.4', '1.2.3.4.', '256.2.3.4', '1.2.3.4/24', '1.2.3'):
            self.assertIsNone(self.net.ipv4(ip))
        for ip, mask in (('192.168.1.1', '255.0.255.0'), ('192.168.1.1', '0.0.0.0'),
                         ('192.168.1.1', '255.255.255.255'), ('192.168.1.1', '255.255.255.254'),
                         ('192.168.1.0', '255.255.255.0'), ('192.168.1.255', '255.255.255.0')):
            result, message = self.net.broadcast(ip, mask)
            self.assertIsNone(result)
            self.assertTrue(message)

    def test_mac_formats_and_invalid_macs(self):
        for mac in ('02:AA:BB:CC:DD:EE', '02-AA-BB-CC-DD-EE', '02AABBCCDDEE'):
            self.assertEqual(self.net.mac(mac), '02:aa:bb:cc:dd:ee')
        for mac in ('', '00:00:00:00:00:00', 'ff:ff:ff:ff:ff:ff', '01:00:5e:00:00:01',
                    '02:aa:bb:cc:dd', '02:aa-bb:cc:dd:ee', 'not a MAC'):
            self.assertIsNone(self.net.mac(mac))

    def test_discovery_excludes_internal_and_loopback(self):
        self.lua.execute('''cards={
            card('imx6', '02:00:00:00:00:01', true, '192.168.33.1', '255.255.255.0'),
            card('Loopback Pseudo-Interface 1', '', true, '127.0.0.1', '255.0.0.0'),
            card('Con2', '02-AA-BB-CC-DD-EE', 'Yes', '169.254.50.10', '255.255.0.0')
        }''')
        entries = self.entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[1].name, 'Con2')
        self.assertEqual(entries[1].mac, '02:aa:bb:cc:dd:ee')
        self.assertEqual(entries[1].broadcast, '169.254.255.255')

    def test_each_ipv4_address_has_its_own_entry(self):
        self.lua.execute('''cards={node({Name='Ethernet', MAC='02:aa:bb:cc:dd:ee', Link=true}, {
            node({IP='192.168.50.10', Mask='255.255.255.0'}),
            node({IP='10.20.4.10', Mask='255.255.254.0'})})}''')
        entries = self.entries()
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[2].broadcast, '10.20.5.255')

    def test_disconnected_and_unaddressed_ports_are_unavailable(self):
        self.lua.execute('''cards={
            card('Con1', '02:00:00:00:00:01', false, '2.0.0.10', '255.0.0.0'),
            card('Con2', '02:00:00:00:00:02', 'No', '', '')}''')
        entries = self.entries()
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[1].problem, 'Link is down')
        self.assertEqual(entries[2].problem, 'No usable IPv4 address')

    def test_interface_api_absent_or_unreadable_falls_back(self):
        for function in ('function() error("unavailable") end', 'function() return {} end',
                         'function() return {Interfaces={}} end'):
            entries, message = self.net.list(self.table(function))
            self.assertEqual(len(entries), 0)
            self.assertIn('Manual / Advanced', message)

    def test_getter_only_properties_are_supported(self):
        self.lua.execute('''
            local function handle(props, kids)
                return {Get=function(self,k) return props[k] end,
                        Children=function() return kids or {} end}
            end
            cards={handle({Name='Con2', MAC='02:aa:bb:cc:dd:ee', Link=true},
                          {handle({IP='169.254.50.10', Mask='255.255.0.0'})})}
        ''')
        self.assertEqual(self.entries()[1].ip, '169.254.50.10')

    def config(self):
        return self.table("{observer=true,interfaceName='Con2',interfaceMAC='02:aa:bb:cc:dd:ee',interfaceIP='169.254.50.10'}")

    def test_selection_fills_all_fields_without_changing_system_interfaces(self):
        self.lua.execute("cards={card('Con2','02:aa:bb:cc:dd:ee',true,'169.254.50.10','255.255.0.0')}")
        cfg = self.config()
        self.net.resolve(cfg, self.lua.globals().Root)
        self.assertEqual(cfg.bind, '*')
        self.assertEqual(cfg.observerIP, '169.254.50.10')
        self.assertEqual(cfg.broadcast, '169.254.255.255')
        self.assertEqual(cfg.mac, '02:aa:bb:cc:dd:ee')
        self.assertEqual(cfg.interfaceMask, '255.255.0.0')
        self.assertEqual(self.entries()[1].ip, '169.254.50.10')

    def test_dhcp_change_follows_same_card_when_address_is_unambiguous(self):
        self.lua.execute("cards={card('Con2','02:aa:bb:cc:dd:ee',true,'192.168.50.10','255.255.255.0')}")
        cfg = self.config()
        self.net.resolve(cfg, self.lua.globals().Root)
        self.assertEqual(cfg.observerIP, '192.168.50.10')
        self.assertEqual(cfg.broadcast, '192.168.50.255')

    def test_missing_replaced_or_ambiguous_interface_is_not_silently_changed(self):
        for setup in (
            'cards={}',
            "cards={card('Con2','02:00:00:00:00:01',true,'169.254.50.10','255.255.0.0')}",
            "cards={node({Name='Con2',MAC='02:aa:bb:cc:dd:ee',Link=true},{node({IP='10.1.1.1',Mask='255.255.255.0'}),node({IP='10.2.2.2',Mask='255.255.255.0'})})}",
        ):
            self.lua.execute(setup)
            with self.assertRaisesRegex(Exception, 'unavailable|addresses changed'):
                self.net.resolve(self.config(), self.lua.globals().Root)

    def test_duplicate_ip_on_different_cards_is_rejected(self):
        self.lua.execute('''cards={
            card('Con2','02:aa:bb:cc:dd:ee',true,'169.254.50.10','255.255.0.0'),
            card('Con3','02:00:00:00:00:03',true,'169.254.50.10','255.255.0.0')}''')
        with self.assertRaisesRegex(Exception, 'multiple interfaces'):
            self.net.resolve(self.config(), self.lua.globals().Root)

    def test_missing_mac_allows_passive_but_not_observer(self):
        self.lua.execute("cards={card('Con2','',true,'169.254.50.10','255.255.0.0')}")
        cfg = self.config()
        cfg.interfaceMAC = ''
        with self.assertRaisesRegex(Exception, 'MAC unavailable'):
            self.net.resolve(cfg, self.lua.globals().Root)
        cfg.observer = False
        self.net.resolve(cfg, self.lua.globals().Root)
        self.assertEqual(cfg.mac, '')

    def test_selected_subnet_filter_and_manual_reception(self):
        cfg = self.table("{interfaceName='Con2',observerIP='10.20.4.10',interfaceMask='255.255.254.0'}")
        self.assertTrue(self.net.accepts(cfg, '10.20.5.98'))
        self.assertFalse(self.net.accepts(cfg, '10.20.6.98'))
        self.assertFalse(self.net.accepts(cfg, 'bad'))
        cfg.interfaceName = ''
        self.assertTrue(self.net.accepts(cfg, '10.20.6.98'))


class HostNetworkTests(unittest.TestCase):
    def setUp(self):
        NetworkTests.setUp(self)
        self.lua.execute('''
            saved={}; dialogs={}; replies={}; timers={}; sockets={}; commands={}
            PluginVars=function() return saved end
            GetVar=function(t,k) return t[k] end
            SetVar=function(t,k,v) t[k]=v end
            ObjectList=function() return {{}} end
            Time=function() return 0 end
            Timer=function(fn) timers[#timers+1]=fn end
            Printf=function() end; ErrPrintf=function() end
            Cmd=function(text) commands[#commands+1]=text; return 'OK' end
            MessageBox=function(options)
                dialogs[#dialogs+1]=options
                local response=table.remove(replies,1)
                if type(response)=='function' then return response(options) end
                return response or {success=false,result=0}
            end
            package.preload.socket=function()
                return {udp4=function()
                    local sock={closed=false,queue={}}
                    sockets[#sockets+1]=sock
                    function sock:settimeout() end
                    function sock:setsockname(ip,port)
                        assert(not self.closed,'bind on closed socket')
                        self.ip=ip; self.port=port
                        if failPort==port then return nil,'address already in use' end
                        return 1
                    end
                    function sock:setoption() return 1 end
                    function sock:close() self.closed=true end
                    function sock:sendto(data) return #data end
                    function sock:receivefrom()
                        assert(not self.closed,'receive on closed socket')
                        local packet=table.remove(self.queue,1)
                        if packet then return packet[1],packet[2] end
                        return nil,'timeout'
                    end
                    return sock
                end}
            end
            cards={card('Con2','02:aa:bb:cc:dd:ee',true,'169.254.50.10','255.255.0.0')}
        ''')
        self.main = self.lua.execute((ROOT / 'dist/grandma3-pro-dj-link.lua').read_text())
        self.service = self.lua.globals().CDJLINK

    def choose(self, observer=True, timeout='5'):
        self.lua.execute('''replies={{success=true,result=1,
            selectors={['Network interface']=1}, inputs={['Disconnect seconds']='5'},
            states={['Announce observer']=''' + ('true' if observer else 'false') + '}}}')
        self.lua.globals().replies[1].inputs['Disconnect seconds'] = timeout
        self.service.networkSettings()

    def test_picker_applies_and_persists_selected_interface(self):
        self.choose()
        cfg = self.service.cfg
        self.assertEqual(cfg.interfaceName, 'Con2')
        self.assertEqual(cfg.observerIP, '169.254.50.10')
        self.assertEqual(self.lua.globals().saved.interfaceMAC, '02:aa:bb:cc:dd:ee')
        self.assertFalse(self.service.engine.armed)

    def test_cancel_does_not_stop_or_disarm_running_service(self):
        self.assertTrue(self.service.start('Live')[0])
        self.service.arm(True)
        previous = len(self.lua.globals().sockets)
        self.service.networkSettings()
        self.assertTrue(self.service.running)
        self.assertTrue(self.service.engine.armed)
        self.assertEqual(len(self.lua.globals().sockets), previous)

    def test_auto_bind_keeps_broadcasts_and_binds_status_and_sender_to_selected_ip(self):
        self.choose()
        self.assertTrue(self.service.start('Live')[0])
        self.assertEqual(self.service.sockets[50000].ip, '*')
        self.assertEqual(self.service.sockets[50001].ip, '*')
        self.assertEqual(self.service.sockets[50002].ip, '169.254.50.10')
        self.assertEqual(self.service.sender.ip, '169.254.50.10')

    def test_selection_change_closes_old_sockets_and_invalidates_old_timer(self):
        self.assertTrue(self.service.start('Live')[0])
        self.service.arm(True)
        previous = [self.service.sockets[p] for p in (50000, 50001, 50002)]
        old_timer = self.lua.globals().timers[1]
        self.choose()
        self.assertTrue(all(sock.closed for sock in previous))
        self.assertTrue(self.service.running)
        self.assertFalse(self.service.engine.armed)
        old_timer()  # Must not touch any of the closed sockets.

    def test_test_mode_ignores_missing_live_interface(self):
        self.choose()
        self.lua.execute('cards={}')
        self.assertTrue(self.service.start('Test')[0])
        self.assertEqual(self.service.sockets[50002].ip, '127.0.0.1')
        self.assertFalse(self.service.engine.armed)

    def test_restart_rechecks_dhcp_address_and_saves_new_values(self):
        self.choose()
        self.lua.execute("cards={card('Con2','02:aa:bb:cc:dd:ee',true,'192.168.50.10','255.255.255.0')}")
        self.assertTrue(self.service.start('Live')[0])
        self.assertEqual(self.service.sockets[50002].ip, '192.168.50.10')
        self.assertEqual(self.lua.globals().saved.broadcast, '192.168.50.255')

    def test_missing_card_or_failed_bind_leaves_no_partial_listener(self):
        self.choose()
        self.lua.execute('failPort=50002')
        self.assertFalse(self.service.start('Live')[0])
        self.assertFalse(self.service.running)
        self.assertTrue(all(sock.closed for sock in self.lua.globals().sockets.values()))
        self.lua.execute('cards={}; failPort=nil')
        previous = len(self.lua.globals().sockets)
        self.assertFalse(self.service.start('Live')[0])
        self.assertEqual(len(self.lua.globals().sockets), previous)

    def test_refresh_and_manual_fallback(self):
        self.lua.execute('''replies={
            {success=true,result=2},
            {success=true,result=1,selectors={['Network interface']=0},
             inputs={['Disconnect seconds']='5'},states={['Announce observer']=false}},
            {success=true,result=1,states={['Announce observer']=false},
             inputs={['Listen IPv4 (* = all)']='*',['Observer interface IPv4']='',
                     ['Subnet broadcast IPv4']='',['Interface MAC']='',['Disconnect seconds']='6'}}
        }''')
        self.service.networkSettings()
        self.assertEqual(len(self.lua.globals().dialogs), 3)
        self.assertEqual(self.service.cfg.interfaceName, '')
        self.assertEqual(self.service.cfg.timeout, 6)

    def test_unavailable_api_retains_manual_configuration(self):
        self.lua.execute('Root=nil')
        self.service.networkSettings()
        dialog = self.lua.globals().dialogs[1]
        self.assertIn('Manual / Advanced', dialog.message)
        self.assertEqual(dialog.selectors[1]['values']['Manual / Advanced'], 0)
        self.assertEqual(self.service.cfg.bind, '*')

    def test_live_poll_filters_packets_outside_selected_subnet(self):
        self.choose()
        self.assertTrue(self.service.start('Live')[0])
        core = self.lua.execute((ROOT / 'src/cdj-link-core.lua').read_text())
        self.lua.globals().testCore = core
        self.lua.execute("CDJLINK.sockets[50001].queue[1]={testCore.beat(1,128,1),'10.1.1.2'}")
        self.lua.globals().timers[1]()
        self.assertEqual(self.service.engine.packets, 0)
        self.lua.execute("CDJLINK.sockets[50001].queue[1]={testCore.beat(1,128,1),'169.254.50.20'}")
        self.lua.globals().timers[2]()
        # Core rejects the synthetic title in Live mode, but receives the allowed subnet.
        self.assertEqual(self.service.engine.packets, 1)

    def test_disappearing_selection_does_not_interrupt_previous_listener(self):
        self.assertTrue(self.service.start('Live')[0])
        self.service.arm(True)
        self.lua.execute('''replies={function()
            cards={}
            return {success=true,result=1,selectors={['Network interface']=1},
                    inputs={['Disconnect seconds']='5'},states={['Announce observer']=true}}
        end}''')
        self.service.networkSettings()
        self.assertTrue(self.service.running)
        self.assertTrue(self.service.engine.armed)
        self.assertEqual(self.service.cfg.interfaceName, '')
        self.assertIn('unavailable', self.lua.globals().dialogs[2].message)

    def test_failed_restart_reports_the_failure_and_closes_partial_sockets(self):
        self.assertTrue(self.service.start('Live')[0])
        self.lua.execute('failPort=50002')
        self.choose()
        self.assertFalse(self.service.running)
        self.assertFalse(self.service.engine.armed)
        self.assertTrue(all(sock.closed for sock in self.lua.globals().sockets.values()))
        self.assertIn('listener could not restart', self.lua.globals().dialogs[2].message)

    def test_existing_manual_preferences_migrate_without_resetting(self):
        self.lua.execute("saved.bind='2.0.0.10'; saved.observerIP='2.0.0.10'; saved.broadcast='2.255.255.255'; saved.mac='02:00:00:00:00:01'; saved.observer=1")
        self.lua.execute((ROOT / 'dist/grandma3-pro-dj-link.lua').read_text())
        cfg = self.lua.globals().CDJLINK.cfg
        self.assertEqual(cfg.bind, '2.0.0.10')
        self.assertEqual(cfg.interfaceName, '')
        self.assertTrue(cfg.observer)
        self.assertEqual(cfg.mac, '02:00:00:00:00:01')

    def test_picker_tracks_identity_when_list_is_reordered(self):
        self.choose()
        self.lua.execute("table.insert(cards,1,card('Con1','02:00:00:00:00:01',true,'2.0.0.10','255.0.0.0'))")
        self.service.networkSettings()
        self.assertEqual(self.lua.globals().dialogs[2].selectors[1].selectedValue, 2)

    def test_missing_mac_or_invalid_timeout_does_not_apply(self):
        self.assertTrue(self.service.start('Live')[0])
        self.service.arm(True)
        self.lua.execute("cards={card('Con2','',true,'169.254.50.10','255.255.0.0')}")
        self.choose()
        self.assertEqual(self.service.cfg.interfaceName, '')
        self.assertTrue(self.service.engine.armed)
        self.assertIn('MAC unavailable', self.lua.globals().dialogs[2].message)
        self.lua.execute("cards={card('Con2','02:aa:bb:cc:dd:ee',true,'169.254.50.10','255.255.0.0')}")
        self.choose(timeout='bad')
        self.assertEqual(self.service.cfg.interfaceName, '')
        self.assertTrue(self.service.engine.armed)
        self.assertIn('Disconnect timeout', self.lua.globals().dialogs[5].message)

    def test_network_argument_opens_picker_without_starting_a_listener(self):
        self.main(None, 'network')
        self.assertFalse(self.service.running)
        self.assertEqual(len(self.lua.globals().sockets), 0)
        self.assertEqual(self.lua.globals().dialogs[1].title, 'PRO DJ LINK - Network Interface')


if __name__ == '__main__':
    unittest.main(verbosity=2)
