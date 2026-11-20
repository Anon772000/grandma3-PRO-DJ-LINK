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
