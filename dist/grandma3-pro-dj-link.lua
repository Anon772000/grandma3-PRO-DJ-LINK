-- grandMA3 PRO DJ LINK v1.1.0-rc.1 | MIT License
local Core = (function()
-- Pure protocol and timing engine. Zero-based protocol offsets, Lua strings are one-based.
local Core = {}
local MAGIC = 'Qspt1WmJOL'
local function u16(s,o) local a,b=s:byte(o+1,o+2); return a*256+b end
local function u32(s,o) local a,b,c,d=s:byte(o+1,o+4); return ((a*256+b)*256+c)*256+d end
local function flag(v,b) return math.floor(v/b)%2==1 end
local function name(s,o) return (s:sub(o+1,o+20):gsub('%z.*',''):gsub('[^%w %-%._]','')) end
function Core.parse(data,port)
    if type(data)~='string' or #data<11 or data:sub(1,10)~=MAGIC then return nil end
    local kind=data:byte(11)
    local p
    if port==50001 and kind==0x28 and #data>=96 then
        local raw,pitch=u16(data,0x5a),u32(data,0x54)
        p={kind='beat',id=data:byte(0x22),name=name(data,0x0b),beat=data:byte(0x5d)}
        if raw==0xffff or pitch==0 or p.beat<1 or p.beat>4 then return nil end
        p.bpm=raw/100*pitch/0x100000
        if p.bpm<20 or p.bpm>300 then return nil end
    elseif port==50000 and kind==0x06 and #data==54 then
        p={kind='presence',id=data:byte(0x25),name=name(data,0x0c)}
    elseif port==50002 and kind==0x0a and #data>=0xd4 then
        local f,mode=data:byte(0x8a),data:byte(0x7c)
        p={kind='status',id=data:byte(0x22),name=name(data,0x0c),counter=u32(data,0xc8),
           master=flag(f,0x20),onAir=flag(f,8),playing=(mode==3 or mode==4 or mode==7 or mode==0x12),
           mode=mode,beat=data:byte(0xa7)}
        local raw,pitch=u16(data,0x92),u32(data,0x8c)
        local bpm=raw/100*pitch/0x100000
        if raw~=0xffff and bpm>=20 and bpm<=300 then p.bpm=bpm end
    end
    if not p or p.id<1 or p.id>6 then return nil end
    p.test=p.name:sub(1,12)=='CDJLINK TEST'
    return p
end

function Core.new(cfg,env)
    local e={cfg=cfg,env=env,players={},events={},armed=false,pending=true,bars=0,
             packets=0,ignored=0,duplicates=0,syncs=0,previews=0,lastSent={},message='Waiting for player'}
    function e:record(text,p,t,live)
        self.events[#self.events+1]={text=text,player=p.id,beat=p.beat,bpm=p.bpm,time=t,live=live}
        if #self.events>160 then table.remove(self.events,1) end
        if self.env.record then self.env.record(self.events[#self.events]) end
    end
    function e:reset()
        self.pending=true; self.bars=0; self.lastSent={}; self.active=nil
    end
    function e:arm(value) self.armed=value; self:reset() end
    function e:connected(p,t) return p and t-(p.lastSeen or -1e9)<=self.cfg.timeout end
    function e:selected(t)
        if self.cfg.player~=0 then
            local p=self.players[self.cfg.player]
            if self:connected(p,t) then return p end
            return nil
        end
        local found
        for _,p in pairs(self.players) do
            if self:connected(p,t) and p.master and t-(p.lastStatus or -1e9)<1.5 then
                if found then self.message='Multiple tempo masters; waiting for handoff'; return nil end
                found=p
            end
        end
        return found
    end
    function e:state(p,t)
        if not self:connected(p,t) then return 'OFFLINE' end
        if t<(p.conflictUntil or 0) then return 'ID CONFLICT' end
        if t-(p.lastStatus or -1e9)<1.5 then
            if p.mode==0 then return 'NO TRACK' end
            if p.mode==2 then return 'LOADING' end
            return p.playing and 'PLAYING' or 'PAUSED / STOPPED'
        end
        local period=60/(p.bpm or 120)
        return t-(p.lastBeat or -1e9)<math.max(1.2,period*2.5) and 'BEATS (play inferred)' or 'CONNECTED / NO BEATS'
    end
    function e:send(text,p,t)
        local live=self.armed
        if live then
            local ok,err=self.env.command(text)
            if not ok then self.message='Command failed: '..tostring(err); self.armed=false; return false end
        end
        self:record(text,p,t,live)
        return true
    end
    function e:ingest(data,port,ip,t)
        self.packets=self.packets+1
        local v=Core.parse(data,port)
        if not v or (self.cfg.input=='Test')~=v.test or (v.test and ip~='127.0.0.1') then
            self.ignored=self.ignored+1; return false
        end
        local p=self.players[v.id]
        if p and p.ip~=ip and self:connected(p,t) then
            p.conflictUntil=t+self.cfg.timeout
            self.message='Player '..v.id..' number conflict; output held'
            return false
        end
        if not p or not self:connected(p,t) then
            p={id=v.id,ip=ip}; self.players[v.id]=p
            if self.active==v.id then self:reset() end
        end
        p.name=v.name; p.test=v.test; p.lastSeen=t
        if v.kind=='status' then
            if p.counter then
                local diff=(v.counter-p.counter)%4294967296
                if (diff==0 or diff>2147483648) and t-(p.lastStatus or -1e9)<3 then return false end
            end
            if p.playing==false and v.playing then self.pending=true end
            p.counter=v.counter; p.lastStatus=t; p.playing=v.playing; p.master=v.master; p.onAir=v.onAir; p.mode=v.mode
            p.bpm=v.bpm or p.bpm
            if not p.lastBeat and v.beat>=1 and v.beat<=4 then p.beat=v.beat end
        elseif v.kind=='beat' then
            local period=60/v.bpm
            if p.lastBeat and t-p.lastBeat<period*0.35 then self.duplicates=self.duplicates+1; return false end
            p.lastBeat=t; p.beat=v.beat; p.bpm=v.bpm
            local selected=self:selected(t)
            if selected~=p or t<(p.conflictUntil or 0) then return true end
            if p.lastStatus and t-p.lastStatus<1.5 and not p.playing then
                self.message='Player paused; output held'; return true
            end
            if self.active~=p.id then self:reset(); self.active=p.id end
            self.message=self.armed and 'Following player '..p.id or 'Monitor only - outputs disarmed'
            local channels={self.cfg.song,self.cfg.bump}
            local valid={}
            for i,c in ipairs(channels) do
                if c.enabled then
                    local bpm=math.floor(v.bpm*c.ratio*100+0.5)/100
                    if bpm<=0 or bpm>225 then
                        self.message='Target '..c.master..' exceeds 225 BPM; reduce multiplier'
                    else
                        valid[#valid+1]=c
                        if not self.lastSent[i] or math.abs(self.lastSent[i]-bpm)>=0.05 then
                            if self:send(string.format('Master %s At BPM %.2f',c.master,bpm),v,t) then
                                self.lastSent[i]=bpm; self.pending=true
                            end
                        end
                    end
                end
            end
            if v.beat==1 then
                self.bars=self.bars+1
                if self.cfg.sync~='Off' and (self.pending or (self.cfg.every>0 and self.bars>=self.cfg.every)) then
                    local sent=false
                    for _,c in ipairs(valid) do
                        if self:send(self.cfg.sync..' Master '..c.master,v,t) then sent=true end
                    end
                    if sent then
                        self.pending=false; self.bars=0
                        if self.armed then self.syncs=self.syncs+1 else self.previews=self.previews+1 end
                        self.lastSync=t
                    end
                end
            end
        end
        return true
    end
    return e
end

-- Packet encoders used by the local test player, sent through actual loopback UDP.
local function packet(n,kind,title,offset)
    local b={}; for i=1,n do b[i]=0 end
    for i=1,#MAGIC do b[i]=MAGIC:byte(i) end
    b[11]=kind
    for i=1,math.min(20,#title) do b[offset+i]=title:byte(i) end
    return b
end
local function put(b,o,n,bytes)
    for i=bytes-1,0,-1 do b[o+i+1]=n%256; n=math.floor(n/256) end
end
local function finish(b) local s={}; for i,v in ipairs(b) do s[i]=string.char(v) end; return table.concat(s) end
function Core.beat(id,bpm,beat)
    local b=packet(96,0x28,'CDJLINK TEST '..id,0x0b)
    b[0x1f+1]=1; b[0x21+1]=id; put(b,0x22,60,2)
    for _,o in ipairs({0x24,0x28,0x2c,0x30,0x34,0x38}) do put(b,o,math.floor(60000/bpm),4) end
    put(b,0x54,0x100000,4); put(b,0x5a,math.floor(bpm*100+0.5),2); b[0x5c+1]=beat; b[0x5f+1]=id
    return finish(b)
end
function Core.presence(id,title,ip,mac)
    local b=packet(54,6,title or ('CDJLINK TEST '..id),0x0c)
    b[0x0b+1]=0; b[0x20+1]=1; b[0x21+1]=2; put(b,0x22,54,2)
    b[0x24+1]=id; b[0x25+1]=1
    for i=1,6 do b[0x26+i]=(mac or {2,0,0,0,0,id})[i] end
    for i=1,4 do b[0x2c+i]=(ip or {127,0,0,1})[i] end
    b[0x30+1]=2; b[0x34+1]=1
    return finish(b)
end
function Core.status(id,bpm,beat,playing,master,counter)
    local b=packet(0xd4,0x0a,'CDJLINK TEST '..id,0x0c)
    b[0x20+1]=3; b[0x21+1]=id; put(b,0x22,0xb0,2); b[0x24+1]=id
    b[0x7b+1]=playing and 3 or 5; b[0x89+1]=(playing and 0x40 or 0)+(master and 0x20 or 0)
    put(b,0x8c,0x100000,4); put(b,0x92,math.floor(bpm*100),2); b[0xa6+1]=beat; put(b,0xc8,counter,4)
    return finish(b)
end
return Core

end)()
local Network = (function()
-- Read the local MA interface collection. Never writes console network settings.
local Network = {}

function Network.ipv4(value)
    if type(value) ~= 'string' or not value:match('^%d+%.%d+%.%d+%.%d+$') then return nil end
    local bytes = {}
    for part in value:gmatch('%d+') do
        local n = tonumber(part)
        if n > 255 then return nil end
        bytes[#bytes + 1] = n
    end
    return bytes
end

local function addressNumber(value)
    local bytes = Network.ipv4(value)
    if not bytes then return nil end
    local n = 0
    for _, b in ipairs(bytes) do n = n * 256 + b end
    return n
end

local function addressString(n)
    return string.format('%d.%d.%d.%d', (n >> 24) & 255, (n >> 16) & 255,
                         (n >> 8) & 255, n & 255)
end

function Network.broadcast(ip, mask)
    local address, bits = addressNumber(ip), addressNumber(mask)
    if not address or not bits then return nil, 'Missing or invalid IPv4 address/subnet mask' end
    local inverse = 0xffffffff ~ bits
    if bits == 0 or (inverse & (inverse + 1)) ~= 0 then return nil, 'Subnet mask is not contiguous' end
    if inverse < 3 then return nil, 'A /31 or /32 network has no usable broadcast address' end
    local prefix, remaining = 32, inverse
    while remaining > 0 do prefix = prefix - 1; remaining = remaining >> 1 end
    local network, broadcast = address & bits, address | inverse
    if address == network or address == broadcast then return nil, 'IPv4 address is a subnet/broadcast address' end
    return addressString(broadcast), prefix
end

function Network.mac(value)
    if type(value) ~= 'string' then return nil end
    local compact
    if value:match('^%x%x:%x%x:%x%x:%x%x:%x%x:%x%x$') or
       value:match('^%x%x%-%x%x%-%x%x%-%x%x%-%x%x%-%x%x$') then
        compact = value:gsub('[:%-]', '')
    elseif #value == 12 and value:match('^%x+$') then compact = value end
    if not compact or compact == '000000000000' then return nil end
    -- An Ethernet interface has a unicast MAC, including locally administered ones.
    if (tonumber(compact:sub(1, 2), 16) & 1) ~= 0 then return nil end
    return (compact:lower():gsub('(%x%x)', '%1:'):sub(1, -2))
end

local function get(object, property)
    local ok, value = pcall(function() return object[property] end)
    if ok and value ~= nil then return value end
    ok, value = pcall(function() return object:Get(property) end)
    if ok then return value end
end

local function children(object)
    local ok, value = pcall(function() return object:Children() end)
    if ok and type(value) == 'table' then return value end
    return nil
end

local function linkState(value)
    if value == false or value == 0 then return false end
    if value == true or value == 1 then return true end
    local text = tostring(value):lower()
    if text == 'no' or text == 'false' or text == 'down' then return false end
    if text == 'yes' or text == 'true' or text == 'up' then return true end
    return nil
end

function Network.list(rootFunction)
    local ok, interfaces = pcall(function() return rootFunction().Interfaces end)
    if not ok or not interfaces then return {}, 'MA interface list is unavailable; use Manual / Advanced.' end
    local cards = children(interfaces)
    if not cards then return {}, 'MA interface list could not be read; use Manual / Advanced.' end
    local entries = {}
    for _, card in ipairs(cards) do
        local name = tostring(get(card, 'Name') or '')
        -- imx6 is the console's internal interface, not an external DJ port.
        if name ~= '' and not name:lower():find('imx6', 1, true) then
            local mac = Network.mac(get(card, 'MAC')) or ''
            local link = linkState(get(card, 'Link'))
            local addresses = children(card) or {}
            local found = false
            for _, address in ipairs(addresses) do
                local ip, mask = get(address, 'IP'), get(address, 'Mask')
                local bytes = Network.ipv4(ip)
                if bytes and bytes[1] > 0 and bytes[1] < 224 and bytes[1] ~= 127 then
                    local broadcast, prefix = Network.broadcast(ip, mask)
                    local entry = {name=name, mac=mac, ip=ip, mask=mask or '',
                                   broadcast=broadcast, prefix=broadcast and prefix or nil, link=link}
                    entry.problem = link == false and 'Link is down' or
                                    (not broadcast and prefix or nil)
                    entries[#entries + 1] = entry
                    found = true
                end
            end
            -- Keep disconnected external ports visible, but exclude loopback cards.
            if not found and not name:lower():find('loopback', 1, true) and
               not name:lower():match('^lo%d*$') then
                entries[#entries + 1] = {name=name, mac=mac, ip='', mask='', link=link,
                                         problem='No usable IPv4 address'}
            end
        end
    end
    return entries
end

function Network.selected(config)
    return type(config.interfaceName) == 'string' and config.interfaceName ~= ''
end

function Network.configure(config, entry)
    assert(entry and not entry.problem, entry and entry.problem or 'Select a network interface')
    assert(entry.broadcast, 'Selected interface has no usable broadcast address')
    assert(not config.observer or entry.mac ~= '', 'MAC unavailable for this interface; use Manual / Advanced')
    config.interfaceName, config.interfaceMAC = entry.name, entry.mac
    config.interfaceIP, config.interfaceMask = entry.ip, entry.mask
    -- Broadcast destinations must be received on wildcard sockets. The status
    -- socket and observer sender bind the selected IP; broadcasts are subnet-filtered.
    config.bind, config.observerIP = '*', entry.ip
    config.broadcast, config.mac = entry.broadcast, entry.mac
    return config
end

function Network.resolve(config, rootFunction)
    local entries, reason = Network.list(rootFunction)
    local candidates, exact = {}, nil
    for _, entry in ipairs(entries) do
        local identity = entry.name == config.interfaceName and
                         (config.interfaceMAC == '' or entry.mac == config.interfaceMAC)
        if identity then
            candidates[#candidates + 1] = entry
            if entry.ip == config.interfaceIP then exact = entry end
        end
    end
    local entry = exact or (#candidates == 1 and candidates[1])
    assert(entry, reason or ('Interface '..config.interfaceName..' is unavailable or its addresses changed; reselect it in Network'))
    for _, other in ipairs(entries) do
        assert(other == entry or other.ip ~= entry.ip or
               (other.name == entry.name and other.mac == entry.mac),
               'Selected IP is assigned to multiple interfaces; correct the network configuration')
    end
    return Network.configure(config, entry)
end

function Network.accepts(config, sourceIP)
    if not Network.selected(config) then return true end
    local source, address, mask = addressNumber(sourceIP), addressNumber(config.observerIP), addressNumber(config.interfaceMask)
    return source ~= nil and address ~= nil and mask ~= nil and (source & mask) == (address & mask)
end

return Network

end)()
-- Appended to the embedded timing/network helpers by tools/build-release.py.
local pluginName,componentName,signals,componentHandle=...
signals=signals or {}
local VERSION='1.1.0-rc.1'
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
