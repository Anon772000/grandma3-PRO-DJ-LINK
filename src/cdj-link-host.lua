-- Appended to the embedded timing/network helpers by tools/build-release.py.
local pluginName,componentName,signals,componentHandle=...
signals=signals or {}
local VERSION='1.1.0'
local defaults={player=1,sync='FastSync',every=1,timeout=5,input='Live',bind='*',observer=false,
    observerIP='',broadcast='',mac='',observerID=7,
    interfaceName='',interfaceMAC='',interfaceIP='',interfaceMask='',
    song={master='3.1',ratio=1,enabled=true},bump={master='3.3',ratio=1,enabled=true}}
local function copy(t) local n={}; for k,v in pairs(t) do n[k]=type(v)=='table' and copy(v) or v end; return n end
local ipv4=Network.ipv4
local function macBytes(s)
    if not tostring(s):match('^%x%x:%x%x:%x%x:%x%x:%x%x:%x%x$') then return nil end
    local a={}; for n in s:gmatch('%x%x') do a[#a+1]=tonumber(n,16) end; return a
end
local function masterOK(s)
    local n=tostring(s):match('^3%.(%d+)$'); n=tonumber(n or '')
    return n and n>=1 and n<=15 and tostring(s)=='3.'..n
end
local prefs=PluginVars('CDJ Link Sync')
local function pref(name) local value=GetVar(prefs,name); return value end -- missing GetVar returns ZERO values
local function loadConfig()
    local c=copy(defaults)
    c.player=tonumber(pref('player') or '') or c.player
    c.every=tonumber(pref('every') or '') or c.every
    c.timeout=tonumber(pref('timeout') or '') or c.timeout
    for _,k in ipairs({'sync','bind','observerIP','broadcast','mac','interfaceName','interfaceMAC','interfaceIP','interfaceMask'}) do c[k]=pref(k) or c[k] end
    c.observer=tonumber(pref('observer') or '0')==1
    for _,k in ipairs({'song','bump'}) do
        c[k].master=pref(k..'Master') or c[k].master
        c[k].ratio=tonumber(pref(k..'Ratio') or '') or c[k].ratio
        c[k].enabled=tonumber(pref(k..'Enabled') or '1')==1
    end
    return c
end
local function validate(c)
    assert(c.player>=0 and c.player<=6 and c.player%1==0,'Player must be Auto or 1-6')
    assert(c.sync=='FastSync' or c.sync=='ReSync' or c.sync=='Off','Invalid sync mode')
    assert(c.every>=0 and c.every<=32 and c.every%1==0,'Sync bars must be 0-32; 0 means on change/request')
    assert(c.timeout>=2 and c.timeout<=30,'Disconnect timeout must be 2-30 seconds')
    assert(c.bind=='*' or ipv4(c.bind),'Listen IP must be * or an IPv4 address')
    for _,k in ipairs({'song','bump'}) do
        local v=c[k]
        assert(masterOK(v.master),'Use master 3.1 through 3.15; 3.16 is the audio BPM master')
        assert(v.ratio>=0.0625 and v.ratio<=4,'Multiplier must be 0.0625 through 4')
        local objects=ObjectList('Master '..v.master)
        assert(objects and #objects==1,'Master '..v.master..' is unavailable')
    end
    assert(not(c.song.enabled and c.bump.enabled and c.song.master==c.bump.master),'Song and Bump must use different masters')
    if c.observer then
        assert(ipv4(c.observerIP) and ipv4(c.broadcast) and macBytes(c.mac),'Observer needs interface IPv4, broadcast IPv4, and MAC aa:bb:cc:dd:ee:ff')
        assert(c.observerIP~='0.0.0.0' and c.observerIP~='127.0.0.1','Use the DJ network interface address for observer announcements')
    end
end
local function saveConfig(c)
    for _,k in ipairs({'player','sync','every','timeout','bind','observerIP','broadcast','mac','interfaceName','interfaceMAC','interfaceIP','interfaceMask'}) do SetVar(prefs,k,c[k]) end
    SetVar(prefs,'observer',c.observer and 1 or 0)
    for _,k in ipairs({'song','bump'}) do
        SetVar(prefs,k..'Master',c[k].master); SetVar(prefs,k..'Ratio',c[k].ratio)
        SetVar(prefs,k..'Enabled',c[k].enabled and 1 or 0)
    end
end

local prior=_G.CDJLINK
if prior and prior.stop then prior.stop() end
local s={version=VERSION,cfg=loadConfig(),running=false,generation=0,sockets={},ui={},error='',drops=0}
_G.CDJLINK=s
local okConfig,badConfig=pcall(validate,s.cfg)
if not okConfig then s.cfg=copy(defaults); s.error='Saved settings reset: '..tostring(badConfig) end
local function makeEngine()
    s.engine=Core.new(s.cfg,{command=function(text)
        local ok,result=pcall(Cmd,text)
        return ok and result=='OK',tostring(result)
    end})
end
makeEngine()
local updateUI
local function closeSocket(sock) if sock then pcall(function()sock:close()end) end end
function s.stop()
    s.running=false; s.generation=s.generation+1
    for _,sock in pairs(s.sockets) do closeSocket(sock) end
    s.sockets={}; closeSocket(s.sender); s.sender=nil; s.engine:arm(false)
    s.sim=nil
    if updateUI then updateUI() end
end
local function send(data,port,host)
    local ok,err=s.sender:sendto(data,host or '127.0.0.1',port)
    assert(ok,'UDP send: '..tostring(err))
end
local function simulate(t)
    local v=s.sim
    if not v or not v.connected then return end
    if t>=v.nextPresence then send(Core.presence(v.id),50000); v.nextPresence=t+1 end
    if t>=v.nextStatus then
        v.counter=v.counter+1; send(Core.status(v.id,v.bpm,v.beat,v.playing,v.master,v.counter),50002); v.nextStatus=t+0.2
    end
    if v.playing and t>=v.nextBeat then
        send(Core.beat(v.id,v.bpm,v.beat),50001)
        v.beat=v.beat%4+1; v.nextBeat=t+60/v.bpm -- never catch up a backlog of synthetic beats
    end
end
local function poll()
    if componentHandle and not IsObjectValid(componentHandle) then s.stop(); return end
    local t=Time()
    if s.cfg.input=='Test' then simulate(t) end
    if s.cfg.input=='Live' and s.cfg.observer and t>=(s.nextAnnounce or 0) then
        send(Core.presence(s.cfg.observerID,'MA3 CDJ LINK',ipv4(s.cfg.observerIP),macBytes(s.cfg.mac)),50000,s.cfg.broadcast)
        s.nextAnnounce=t+1.5
    end
    -- Discovery and status first. Coalesce queued beats to avoid replaying old downbeats after a UI stall.
    for _,port in ipairs({50000,50002}) do
        for n=1,32 do
            local data,ip=s.sockets[port]:receivefrom()
            if not data then if ip~='timeout' then error('UDP '..port..': '..tostring(ip)) end; break end
            if s.cfg.input=='Test' or Network.accepts(s.cfg,ip) then s.engine:ingest(data,port,ip,t) end
        end
    end
    local latest={}
    for n=1,64 do
        local data,ip=s.sockets[50001]:receivefrom()
        if not data then if ip~='timeout' then error('UDP 50001: '..tostring(ip)) end; break end
        local p=Core.parse(data,50001)
        if p and (s.cfg.input=='Test' or Network.accepts(s.cfg,ip)) then
            local key=ip..'/'..p.id
            if latest[key] then s.drops=s.drops+1 end
            latest[key]={data=data,ip=ip}
        else s.engine.ignored=s.engine.ignored+1 end
    end
    local lag=s.lastPoll and t-s.lastPoll>0.35
    for _,p in pairs(latest) do
        if lag then s.drops=s.drops+1 else s.engine:ingest(p.data,50001,p.ip,t) end
    end
    s.lastPoll=t
    if t-(s.lastUI or 0)>0.15 then updateUI(); s.lastUI=t end
end
local function schedule(generation)
    Timer(function()
        if not s.running or generation~=s.generation then return end
        local ok,err=pcall(poll)
        if not ok then s.error=tostring(err); s.stop(); ErrPrintf('CDJ Link: '..s.error); return end
        if s.running and generation==s.generation then schedule(generation) end
    end,0.01,1)
end
function s.start(mode)
    s.stop(); s.cfg.input=mode or s.cfg.input; s.error=''; s.drops=0; s.lastPoll=nil; s.nextAnnounce=0
    local ok,err=pcall(function()
        if s.cfg.input=='Live' and Network.selected(s.cfg) then Network.resolve(s.cfg,Root) end
        validate(s.cfg)
        local socket=require('socket'); local ctor=socket.udp4 or socket.udp
        for _,port in ipairs({50000,50001,50002}) do
            local sock=assert(ctor()); s.sockets[port]=sock; sock:settimeout(0)
            local bind=s.cfg.input=='Test' and '127.0.0.1' or
                       (port==50002 and Network.selected(s.cfg) and s.cfg.observerIP or s.cfg.bind)
            local bound,why=sock:setsockname(bind,port)
            assert(bound,'Cannot bind UDP '..port..': '..tostring(why))
        end
        s.sender=assert(ctor()); s.sender:settimeout(0)
        if s.cfg.input=='Live' and (Network.selected(s.cfg) or s.cfg.observer) then
            assert(s.sender:setsockname(s.cfg.observerIP,0))
            if s.cfg.observer then assert(s.sender:setoption('broadcast',true)) end
        end
        makeEngine(); s.running=true
        if s.cfg.input=='Test' then
            s.sim={id=s.cfg.player>0 and s.cfg.player or 1,bpm=128,playing=true,master=true,connected=true,
                   beat=1,counter=0,nextBeat=Time()+0.2,nextStatus=0,nextPresence=0}
        end
        schedule(s.generation)
        if s.cfg.input=='Live' and Network.selected(s.cfg) then saveConfig(s.cfg) end
    end)
    if not ok then s.error=tostring(err); s.stop(); ErrPrintf('CDJ Link: '..s.error) end
    updateUI()
    return ok,err
end
function s.configure(c)
    validate(c); local mode=s.cfg.input; local wasRunning=s.running
    s.stop(); s.cfg=copy(c); s.cfg.input=mode; saveConfig(s.cfg); makeEngine()
    if wasRunning then return s.start(mode) end
    return true
end
function s.arm(value)
    if value and not s.running then return false,'Start the listener first' end
    s.engine:arm(value); updateUI(); return true
end
function s.snapshot(path)
    local f=assert(io.open(path,'w'))
    f:write('running=',tostring(s.running),' input=',s.cfg.input,' armed=',tostring(s.engine.armed),' error=',s.error,'\n')
    f:write('packets=',s.engine.packets,' ignored=',s.engine.ignored,' duplicates=',s.engine.duplicates,' drops=',s.drops,' syncs=',s.engine.syncs,' previews=',s.engine.previews,'\n')
    for id,p in pairs(s.engine.players) do f:write('player=',id,' state=',s.engine:state(p,Time()),' bpm=',p.bpm or 0,' beat=',p.beat or 0,' master=',tostring(p.master),' ip=',p.ip,'\n') end
    for _,v in ipairs(s.engine.events) do f:write(string.format('%.6f | %s | player=%d beat=%d bpm=%.2f | %s\n',v.time,v.live and 'LIVE' or 'PREVIEW',v.player,v.beat,v.bpm,v.text)) end
    f:close()
end

local function masterSettings()
    local c=s.cfg
    local r=MessageBox({title='CDJ Link - Master Routing',message='Speed masters 3.1 to 3.15. This show uses SongSpd 3.1 and BumpRate 3.3.',
        commands={{value=1,name='Apply'},{value=0,name='Cancel'}},inputs={{name='Song master',value=c.song.master},{name='Bump master',value=c.bump.master}}})
    if not r.success or r.result~=1 then return end
    local n=copy(c); n.song.master=r.inputs['Song master']; n.bump.master=r.inputs['Bump master']
    local ok,err=pcall(s.configure,n)
    if not ok then MessageBox({title='Routing not applied',message=tostring(err),commands={{value=1,name='OK'}}}) end
end
local function settings()
    local c=s.cfg
    local result=MessageBox({title='CDJ Link - Sync Settings',message='Sync on beat 1. Bars 0 = on change/request.\nApply disarms output. Target limit: 225 BPM.',
        commands={{value=1,name='Apply'},{value=2,name='Masters...'},{value=0,name='Cancel'}},
        inputs={{name='Song multiplier',value=tostring(c.song.ratio),order=1},
                {name='Bump multiplier',value=tostring(c.bump.ratio),order=2},
                {name='Sync every bars',value=tostring(c.every),order=3}},
        states={{name='Song enabled',state=c.song.enabled},{name='Bump enabled',state=c.bump.enabled}},
        selectors={{name='Follow player',selectedValue=c.player,values={['Tempo Master']=0,['A (1)']=1,['B (2)']=2,['C (3)']=3,['D (4)']=4,['E (5)']=5,['F (6)']=6},type=0,order=1},
                   {name='Sync mode',selectedValue=c.sync=='FastSync' and 1 or c.sync=='ReSync' and 2 or 3,values={FastSync=1,ReSync=2,['BPM only']=3},type=1,order=2}}})
    if not result.success then return end
    if result.result==2 then masterSettings(); return end
    if result.result~=1 then return end
    local n=copy(c)
    n.player=result.selectors['Follow player']; n.sync=({'FastSync','ReSync','Off'})[result.selectors['Sync mode']]
    n.every=tonumber(result.inputs['Sync every bars']) or -1
    for _,k in ipairs({'song','bump'}) do local label=k=='song' and 'Song' or 'Bump'; n[k]={master=c[k].master,ratio=tonumber(result.inputs[label..' multiplier']) or -1,enabled=result.states[label..' enabled']} end
    local ok,err=pcall(s.configure,n)
    if not ok then MessageBox({title='Settings not applied',message=tostring(err),commands={{value=1,name='OK'}}}) end
end
local function applyNetwork(c)
    local ok,started,err=pcall(s.configure,c)
    if not ok or not started then
        MessageBox({title='Network settings',message=not ok and tostring(started) or
                    ('Settings saved; listener could not restart: '..tostring(err)),commands={{value=1,name='OK'}}})
    end
end
local function manualNetworkSettings(c)
    local r=MessageBox({title='CDJ Link - Network',message='Passive mode receives presence and beat broadcasts.\nExact play/pause and automatic master following need status packets.\nEnable observer announcements with the DJ interface IP, MAC and subnet broadcast. Observer uses device 7.\nNo track downloads, audio, or CDJ transport commands are sent.',
        commands={{value=1,name='Apply'},{value=0,name='Cancel'}},states={{name='Announce observer',state=c.observer}},
        inputs={{name='Listen IPv4 (* = all)',value=c.bind,order=1},{name='Observer interface IPv4',value=c.observerIP,order=2},
                {name='Subnet broadcast IPv4',value=c.broadcast,order=3},{name='Interface MAC',value=c.mac,order=4},
                {name='Disconnect seconds',value=tostring(c.timeout),order=5}}})
    if not r.success or r.result~=1 then return end
    local n=copy(c); n.bind=r.inputs['Listen IPv4 (* = all)']; n.observer=r.states['Announce observer']
    n.interfaceName=''; n.interfaceMAC=''; n.interfaceIP=''; n.interfaceMask=''
    n.observerIP=r.inputs['Observer interface IPv4']; n.broadcast=r.inputs['Subnet broadcast IPv4']; n.mac=r.inputs['Interface MAC']
    n.timeout=tonumber(r.inputs['Disconnect seconds']) or -1
    applyNetwork(n)
end
local function networkSettings()
    while true do
        local c=s.cfg
        local entries,reason=Network.list(Root)
        local values={['Manual / Advanced']=0}; local selected=0
        for i,entry in ipairs(entries) do
            local detail=entry.ip~='' and entry.ip..(entry.prefix and (' /'..entry.prefix) or '') or 'No IPv4'
            local label=string.format('%d: %s - %s%s',i,entry.name,detail,entry.problem and ' (unavailable)' or '')
            values[label]=i
            if entry.name==c.interfaceName and entry.ip==c.interfaceIP and
               (c.interfaceMAC=='' or entry.mac==c.interfaceMAC) then selected=i end
        end
        local current=Network.selected(c) and
            string.format('\nCurrent: %s / %s\nMask: %s | Broadcast: %s\nMAC: %s',c.interfaceName,c.observerIP,c.interfaceMask,c.broadcast,c.mac) or ''
        local r=MessageBox({title='PRO DJ LINK - Network Interface',
            message='Choose the DJ interface to fill IP, MAC and broadcast automatically.\nEnable observer announcements for play/pause and tempo-master status.\nApply disarms output. Console network settings are not changed.'..current..(reason and ('\n'..reason) or ''),
            commands={{value=1,name='Apply'},{value=2,name='Refresh'},{value=0,name='Cancel'}},
            selectors={{name='Network interface',selectedValue=selected,values=values,type=0}},
            inputs={{name='Disconnect seconds',value=tostring(c.timeout)}},
            states={{name='Announce observer',state=c.observer}}})
        if not r.success or r.result==0 then return end
        if r.result==1 then
            local index=r.selectors['Network interface']
            local n=copy(c); n.observer=r.states['Announce observer']; n.timeout=tonumber(r.inputs['Disconnect seconds']) or -1
            if index==0 then manualNetworkSettings(n); return end
            local ok,err=pcall(function()
                Network.configure(n,entries[index])
                Network.resolve(n,Root) -- Recheck the live address after the selection dialog.
                validate(n)
            end)
            if not ok then
                MessageBox({title='Interface not applied',message=tostring(err),commands={{value=1,name='OK'}}})
            else applyNetwork(n); return end
        end
    end
end
local function testSettings()
    if not s.sim then return end
    local v=s.sim
    local r=MessageBox({title='Loopback Test CDJ',message='Synthetic Pro DJ Link packets travel over 127.0.0.1 only.\nThis tests the same UDP receiver and downbeat logic as a physical CDJ.',
        commands={{value=1,name='Apply'},{value=0,name='Cancel'}},inputs={{name='Test BPM',value=tostring(v.bpm)}},
        states={{name='Playing',state=v.playing},{name='Connected',state=v.connected},{name='Tempo master',state=v.master}},
        selectors={{name='Test player',selectedValue=v.id,values={A=1,B=2,C=3,D=4,E=5,F=6},type=1}}})
    if not r.success or r.result~=1 then return end
    local bpm=tonumber(r.inputs['Test BPM'])
    if not bpm or bpm<20 or bpm>300 then return end
    v.id=r.selectors['Test player']; v.bpm=bpm; v.playing=r.states.Playing; v.connected=r.states.Connected; v.master=r.states['Tempo master']
    v.nextStatus=0; v.nextPresence=0; v.nextBeat=Time()+0.1; s.engine.pending=true
end
local function setText(o,text) if o and IsObjectValid(o) then o.Text=text end end
local function role(p,t)
    if not p or not s.engine:connected(p,t) then return '--' end
    if p.lastStatus and t-p.lastStatus<1.5 then return p.master and 'MASTER' or p.onAir and 'ON AIR' or 'PLAYER' end
    return 'Status unknown'
end
updateUI=function()
    if not s.ui.dialog or not IsObjectValid(s.ui.dialog) then s.ui={}; return end
    local t=Time(); local e=s.engine; local selected=s.running and e:selected(t) or nil
    local output=e.armed and 'OUTPUT ARMED' or 'MONITOR ONLY'
    setText(s.ui.summary,string.format('%s  |  %s  |  %s  |  %s',s.running and 'LISTENING' or 'STOPPED',s.cfg.input,output,selected and ('Following '..string.char(64+selected.id)..' / CDJ '..selected.id) or 'Waiting for selected player'))
    setText(s.ui.config,string.format('Song: Master %s x%g  |  Bump: Master %s x%g  |  %s / %s  |  Syncs %d  Preview %d',s.cfg.song.master,s.cfg.song.ratio,s.cfg.bump.master,s.cfg.bump.ratio,s.cfg.sync,s.cfg.every==0 and 'on change' or ('every '..s.cfg.every..' bar(s)'),e.syncs,e.previews))
    for id=1,6 do
        local p=e.players[id]; local row=s.ui.players[id]
        local connected=s.running and e:connected(p,t)
        local texts={string.char(64+id)..' / CDJ '..id,connected and p.ip or '--',s.running and e:state(p,t) or 'NOT LISTENING',connected and p.bpm and string.format('%.2f',p.bpm) or '--',connected and p.beat and tostring(p.beat)..' / 4' or '--',s.running and role(p,t) or '--'}
        for i,o in ipairs(row) do setText(o,texts[i]); o.TextColor=connected and (selected==p and 'Global.SuccessText' or 'Global.Text') or 'Global.DisabledText' end
    end
    local beat=selected and selected.beat
    setText(s.ui.beats,beat and string.format('%s    %s    %s    %s',beat==1 and '[ 1 ]' or '  1  ',beat==2 and '[ 2 ]' or '  2  ',beat==3 and '[ 3 ]' or '  3  ',beat==4 and '[ 4 ]' or '  4  ') or '1        2        3        4')
    setText(s.ui.hint,s.error~='' and s.error or string.format('%s | UDP %d | ignored %d | duplicates %d | late drops %d',e.message,e.packets,e.ignored,e.duplicates,s.drops))
    setText(s.ui.listen,s.running and 'Stop listener' or 'Start listener')
    setText(s.ui.arm,e.armed and 'Disarm output' or 'Arm output')
    setText(s.ui.mode,s.cfg.input=='Test' and 'Use real CDJs' or 'Test CDJ')
    if s.sim then setText(s.ui.play,s.sim.playing and 'Test: Pause' or 'Test: Play'); setText(s.ui.disconnect,s.sim.connected and 'Test: Disconnect' or 'Test: Connect') end
end

local function closeUI() if s.ui.dialog and IsObjectValid(s.ui.dialog) then s.ui.dialog:Parent():Remove(s.ui.dialog:Index()) end; s.ui={} end
local function guarded(fn) return function() local ok,err=pcall(fn); if not ok then s.error=tostring(err); ErrPrintf('CDJ Link UI: '..s.error) end; updateUI() end end
local function dashboard(display)
    closeUI()
    local d=(display or GetFocusDisplay()).ScreenOverlay:Append('BaseInput')
    s.ui.dialog=d; d.Name='grandMA3 PRO DJ LINK'; d.W=1100; d.H=620; d.Columns=1; d.Rows=2; d.AutoClose=false
    d[1][1].SizePolicy='Fixed'; d[1][1].Size='50'; d[1][2].SizePolicy='Fixed'; d[1][2].Size='570'
    local bar=d:Append('TitleBar'); bar.Columns=2; bar.Rows=1; bar.Anchors='0,0'; bar[2][2].SizePolicy='Fixed'; bar[2][2].Size='50'
    local title=bar:Append('TitleButton'); title.Anchors='0,0'; title.Text='PRO DJ LINK  /  Song + Bump Sync'; title.Texture='corner1'
    local x=bar:Append('CloseButton'); x.Anchors='1,0'; x.Texture='corner2'
    local frame=d:Append('DialogFrame'); frame.Anchors='0,1'; frame.W='100%'; frame.H='100%'; frame.Columns=1; frame.Rows=13
    local sizes={42,42,28,38,38,38,38,38,38,50,52,52,52}
    for i,size in ipairs(sizes) do frame[1][i].SizePolicy='Fixed'; frame[1][i].Size=tostring(size) end
    local function label(parent,text,anchor,font)
        local o=parent:Append('UIObject'); o.Anchors=anchor; o.Text=text; o.Font=font or 'Regular18'; o.BackColor='Global.Transparent'; o.Interactive=false; o.TextAutoAdjust=true; return o
    end
    local function row(index,columns) local r=frame:Append('UILayoutGrid'); r.Anchors='0,'..index; r.Columns=columns; r.Rows=1; r.W='100%'; r.H='100%'; return r end
    s.ui.summary=label(row(0,1),'','0,0','Regular24')
    s.ui.config=label(row(1,1),'','0,0','Regular18')
    local headings={'Player','Connection / IPv4','Transport','BPM','Beat','Role'}
    local header=row(2,6); for i,v in ipairs(headings) do label(header,v,(i-1)..',0') end
    s.ui.players={}
    for id=1,6 do local r=row(id+2,6); s.ui.players[id]={}; for col=1,6 do s.ui.players[id][col]=label(r,'--',(col-1)..',0') end end
    s.ui.beats=label(row(9,1),'','0,0','Regular32')
    local function button(parent,col,text,key,fn)
        local o=parent:Append('Button'); o.Anchors=col..',0'; o.Text=text; o.Font='Regular18'; o.TextAutoAdjust=true; o.PluginComponent=componentHandle; o.Clicked=key
        signals[key]=guarded(fn); return o
    end
    local actions=row(10,6)
    s.ui.listen=button(actions,0,'Start listener','CDJListen',function() if s.running then s.stop() else s.start() end end)
    s.ui.arm=button(actions,1,'Arm output','CDJArm',function() s.arm(not s.engine.armed) end)
    button(actions,2,'Sync settings','CDJSettings',settings)
    button(actions,3,'Network','CDJNetwork',networkSettings)
    button(actions,4,'Sync next beat 1','CDJSync',function() s.engine.pending=true; s.engine.message='Sync queued for next received beat 1' end)
    s.ui.mode=button(actions,5,'Test CDJ','CDJMode',function() s.start(s.cfg.input=='Test' and 'Live' or 'Test') end)
    local test=row(11,6)
    s.ui.play=button(test,0,'Test: Play/Pause','CDJPlay',function() if s.sim then s.sim.playing=not s.sim.playing; s.sim.nextStatus=0; s.sim.nextBeat=Time()+0.1 end end)
    button(test,1,'Test BPM -1','CDJMinus',function() if s.sim then s.sim.bpm=math.max(20,s.sim.bpm-1) end end)
    button(test,2,'Test BPM +1','CDJPlus',function() if s.sim then s.sim.bpm=math.min(300,s.sim.bpm+1) end end)
    button(test,3,'Test settings','CDJTestSettings',testSettings)
    button(test,4,'Test: Reset bar','CDJTestReset',function() if s.sim then s.sim.beat=1; s.sim.nextBeat=Time()+0.1; s.engine.pending=true end end)
    s.ui.disconnect=button(test,5,'Test: Disconnect','CDJDisconnect',function() if s.sim then s.sim.connected=not s.sim.connected; s.sim.nextStatus=0; s.sim.nextPresence=0; s.sim.nextBeat=Time()+0.1 end end)
    s.ui.hint=label(row(12,1),'','0,0','Regular18')
    updateUI()
end
function s.show(display) dashboard(display) end
function s.networkSettings() networkSettings() end
local function main(display,argument)
    local action=tostring(argument or ''):lower()
    if action=='network' then networkSettings(); return end
    if action=='stop' then s.stop(); return end
    if action=='test' then s.start('Test') elseif action=='start' then s.start('Live') elseif action=='arm' then s.arm(true) elseif action=='disarm' then s.arm(false) end
    if action~='arm' and action~='disarm' then
        if not s.running and action=='' then s.start('Live') end
        dashboard(display)
    end
end
return main
