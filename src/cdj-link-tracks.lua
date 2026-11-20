-- Read-only CDJ-3000 track display. Separate, nonblocking TCP work from beat output.
-- Protocol references and scope: docs/track-display.md. No audio/transport requests.
local Tracks = {}
local LIMIT = 2*1024*1024
local MAGIC = 'Qspt1WmJOL'
local function uint(s,offset,size)
    local n=0; for i=offset+1,offset+size do n=n*256+s:byte(i) end; return n
end
local function bytes(n,size)
    local b={}; for i=size,1,-1 do b[i]=string.char(n%256); n=math.floor(n/256) end
    return table.concat(b)
end
function Tracks.number(n,size) size=size or 4; return {tag=6,wire=string.char(({[1]=15,[2]=16,[4]=17})[size])..bytes(n,size)} end
function Tracks.blob(s) return {tag=3,wire=#s>0 and ('\20'..bytes(#s,4)..s) or ''} end
function Tracks.text(s)
    local b={}; for _,c in utf8.codes(s) do
        if c>65535 then local n=c-65536; b[#b+1]=bytes(0xd800+math.floor(n/1024),2)..bytes(0xdc00+n%1024,2)
        else b[#b+1]=bytes(c,2) end
    end
    b[#b+1]='\0\0'; local raw=table.concat(b)
    return {tag=2,wire='\38'..bytes(#raw/2,4)..raw}
end
function Tracks.message(tx,kind,args)
    args=args or {}; assert(#args<=12,'Too many dbserver arguments')
    local tags,body={},{}
    for i=1,12 do tags[i]=string.char(args[i] and args[i].tag or 0) end
    for _,a in ipairs(args) do body[#body+1]=a.wire end
    return Tracks.number(0x872349ae).wire..Tracks.number(tx).wire..Tracks.number(kind,2).wire..
           Tracks.number(#args,1).wire..Tracks.blob(table.concat(tags)).wire..table.concat(body)
end
local function utf16(raw)
    local result={}; local i=1
    while i<#raw do
        local c=uint(raw,i-1,2); i=i+2
        if c==0 then break end
        if c>=0xd800 and c<=0xdbff then
            local low=i<#raw and uint(raw,i-1,2) or 0
            if low>=0xdc00 and low<=0xdfff then c=0x10000+(c-0xd800)*1024+low-0xdc00; i=i+2
            else c=0xfffd end
        elseif c>=0xdc00 and c<=0xdfff then c=0xfffd end
        if c<32 or c==127 then c=32 end
        result[#result+1]=utf8.char(c)
    end
    return table.concat(result)
end
local function field(s,at)
    local tag=s:byte(at); if not tag then return nil end
    local size=({[15]=1,[16]=2,[17]=4})[tag]
    if size then
        if #s<at+size then return nil end
        return uint(s,at,size),at+size+1,6
    end
    assert(tag==20 or tag==38,'Unknown dbserver field')
    if #s<at+4 then return nil end
    size=uint(s,at,4); if tag==38 then size=size*2 end
    assert(size<=LIMIT and (tag~=38 or size<=8192),'Oversized dbserver field')
    if #s<at+4+size then return nil end
    local raw=s:sub(at+5,at+4+size)
    return tag==38 and utf16(raw) or raw,at+5+size,tag==38 and 2 or 3
end
function Tracks.readMessage(s)
    assert(#s<=LIMIT+65536,'Oversized dbserver response')
    local at=1; local head={}
    for i=1,5 do
        local value,nextAt,tag=field(s,at); if not nextAt then return nil end
        assert(tag==(i==5 and 3 or 6),'Invalid dbserver header')
        head[i]=value; at=nextAt
    end
    assert(head[1]==0x872349ae and head[4]<=12 and #head[5]==12,'Invalid dbserver message')
    local args={}
    for i=1,head[4] do
        local expected=head[5]:byte(i)
        if expected==3 and args[i-1]==0 then args[i]='' -- zero-sized blobs have no field
        else
            local value,nextAt,tag=field(s,at); if not nextAt then return nil end
            assert(tag==expected,'Wrong dbserver argument type'); args[i]=value; at=nextAt
        end
    end
    return {tx=head[2],kind=head[3],args=args},at-1
end
function Tracks.position(data)
    if type(data)~='string' or #data<0x3c or data:sub(1,10)~=MAGIC or data:byte(11)~=0x0b then return nil end
    local id=data:byte(0x22); local length=uint(data,0x24,4); local position=uint(data,0x28,4)
    if id<1 or id>6 or length==0 or length>86400 or position>length*1000+2000 then return nil end
    local model=data:sub(12,31):gsub('%z.*','')
    return {id=id,duration=length,position=math.min(position,length*1000),test=model:sub(1,12)=='CDJLINK TEST'}
end
function Tracks.testPosition(id,position,duration)
    local model=('CDJLINK TEST '..id); model=model..string.rep('\0',20-#model)
    return MAGIC..'\11'..model..'\1\0'..string.char(id)..bytes(24,2)..bytes(duration,4)..
           bytes(math.floor(position),4)..bytes(0,4)..string.rep('\0',8)..bytes(1280,4)
end
function Tracks.clock(milliseconds)
    if not milliseconds then return '--:--' end
    local sec=math.floor(math.max(0,milliseconds)/1000)
    return string.format('%d:%02d',math.floor(sec/60),sec%60)
end
function Tracks.bins(raw,columns,startFraction,endFraction,detail)
    local result={}; if not raw or #raw==0 then return result end
    local count=detail and #raw or math.floor(#raw/2)
    local first=math.floor((startFraction or 0)*count)
    local last=math.ceil((endFraction or 1)*count)
    for col=1,columns do
        local low=first+math.floor((col-1)*(last-first)/columns)
        local high=first+math.floor(col*(last-first)/columns)-1
        local height=0
        for index=math.max(0,low),math.min(count-1,math.max(low,high)) do
            height=math.max(height,raw:byte(detail and index+1 or index*2+1)%32)
        end
        result[col]=height
    end
    return result
end
local function close(sock) if sock then pcall(function() sock:close() end) end end
local function identity(p)
    return table.concat({p.ip or '',p.source or 0,p.slot or 0,p.trackType or 0,p.trackId or 0},'/')
end
function Tracks.new(options)
    local self={options=options,players={},status='Track metadata disabled',nextTry={},cursor=0}
    function self:stop() if self.job then close(self.job.socket) end; self.job=nil end
    function self:fail(message,t)
        local job=self.job
        if job then
            local track=self.players[job.player]
            if track and track.key==job.key then track.status=message end
            self.nextTry[job.key]=t+15
        end
        self.status=message; self:stop()
    end
    function self:observe(p,t)
        local key=identity(p); local track=self.players[p.id]
        if not track or track.owner~=p or track.key~=key or p.mode==0 or p.mode==2 then
            if track then self.nextTry[track.key]=nil end
            if self.job and self.job.player==p.id then self:stop() end
            track={key=key,owner=p,ip=p.ip,source=p.source,slot=p.slot,trackType=p.trackType,trackId=p.trackId,
                   status=p.mode==0 and 'No track' or (options.enabled and 'Waiting for track metadata' or 'Metadata downloads disabled')}
            self.players[p.id]=track
        end
        track.lastStatus=t
    end
    function self:position(v,ip,t,engine)
        local p=engine.players[v.id]
        if not p or p.ip~=ip or not engine:connected(p,t) or (options.test~=v.test) then return false end
        local track=self.players[v.id]; if not track or p.mode==0 or p.mode==2 then return false end
        track.position=v.position; track.duration=v.duration; track.positionAt=t
        return true
    end
    function self:view(id,t)
        local track=self.players[id]; if not track then return {} end
        local fresh=track.positionAt and t-track.positionAt<1
        local duration=track.duration or track.metadataDuration
        return {title=track.title,artist=track.artist,status=track.status,duration=duration,
                position=fresh and track.position or nil,
                remaining=fresh and duration and math.max(0,duration*1000-track.position) or nil,
                fraction=fresh and duration and math.min(1,track.position/(duration*1000)) or nil,
                preview=track.preview,detail=track.detail,test=options.test}
    end
    local function rmst(job,menu) return Tracks.number(job.asking*0x1000000+menu*0x10000+job.slot*256+job.trackType) end
    function self:request(kind,args,stage,t)
        local job=self.job; job.tx=job.tx+1; job.output=Tracks.message(job.tx,kind,args)
        job.sent=0; job.stage=stage; job.deadline=t+3; job.buffer=''; job.connected=true
    end
    function self:open(ip,port,stage,t)
        local job=self.job; close(job.socket)
        job.socket=assert(options.socket.tcp()); job.socket:settimeout(0)
        if options.localIP and options.localIP~='' and options.localIP~='*' then assert(job.socket:bind(options.localIP,0)) end
        job.ip=ip; job.port=port; job.stage=stage; job.connected=false; job.deadline=t+3; job.buffer=''; job.output=''; job.sent=0
    end
    function self:complete(t)
        local job=self.job; local track=self.players[job.player]
        track.status=track.title and 'Metadata loaded' or 'Title unavailable'
        if not track.preview then track.status=track.status..' / preview unavailable' end
        if not track.detail then track.status=track.status..' / detail unavailable' end
        track.done=not options.waveform or (track.preview and track.detail) or track.attempts>=3
        self.status=track.status; self.nextTry[job.key]=t+30
        close(job.socket); self.job=nil
    end
    function self:waveform(t,detail)
        local job=self.job
        if not options.waveform then self:complete(t); return end
        if detail then self:request(0x2904,{rmst(job,1),Tracks.number(job.trackId),Tracks.number(0)},'detail',t)
        else self:request(0x2004,{rmst(job,8),Tracks.number(1),Tracks.number(job.trackId),Tracks.number(0)},'preview',t) end
    end
    function self:response(msg,t)
        local job=self.job; local track=self.players[job.player]; local a=msg.args
        assert(msg.tx==(job.stage=='setup' and 0xfffffffe or job.tx),'Unexpected metadata transaction')
        if job.stage=='setup' then
            assert(msg.kind==0x4000 and a[1]==0 and a[2]==job.target,'Metadata setup refused')
            local kind=(job.trackType==1 or job.trackType==6) and 0x2002 or 0x2202
            self:request(kind,{rmst(job,1),Tracks.number(job.trackId)},'metadata',t)
            job.requestKind=kind
        elseif job.stage=='metadata' then
            assert(msg.kind==0x4000 and a[1]==job.requestKind and type(a[2])=='number' and a[2]>0 and a[2]<=64,'Metadata unavailable')
            job.count=a[2]; job.items=0; job.header=false
            self:request(0x3000,{rmst(job,1),Tracks.number(0),Tracks.number(a[2]),Tracks.number(0),Tracks.number(a[2]),Tracks.number(0)},'menu',t)
        elseif job.stage=='menu' then
            if msg.kind==0x4001 then assert(not job.header,'Duplicate menu header'); job.header=true
            elseif msg.kind==0x4101 then
                assert(job.header and #a>=7 and type(a[7])=='number','Invalid metadata menu')
                job.items=job.items+1; assert(job.items<=job.count,'Too many metadata items')
                local kind=a[7]%256
                local label=type(a[4])=='string' and a[4] or nil
                if label then local finish=utf8.offset(label,257); if finish then label=label:sub(1,finish-1) end end
                if kind==4 then track.title=label
                elseif kind==7 then track.artist=label
                elseif kind==11 and type(a[2])=='number' and a[2]>0 and a[2]<=86400 then track.metadataDuration=a[2] end
            elseif msg.kind==0x4201 then
                assert(job.header and job.items==job.count,'Incomplete metadata menu')
                self:waveform(t,false)
            else error('Unexpected metadata menu response') end
        elseif job.stage=='preview' or job.stage=='detail' then
            local detail=job.stage=='detail'; local kind=detail and 0x4a02 or 0x4402
            if msg.kind~=0x4003 then
                assert(msg.kind==kind and a[1]==(detail and 0x2904 or 0x2004) and type(a[3])=='number' and type(a[4])=='string' and #a[4]==a[3],'Invalid waveform response')
                if #a[4]>0 then track[detail and 'detail' or 'preview']=a[4] end
            end
            if detail then self:complete(t) else self:waveform(t,true) end
        end
    end
    function self:work(t)
        local job=self.job
        assert(t<=job.deadline,'Metadata request timed out')
        if not job.connected then
            local connected,why=job.socket:connect(job.ip,job.port)
            if not connected and why~='already connected' then
                assert(why=='timeout' or why=='Operation already in progress' or why=='Operation now in progress','TCP connect: '..tostring(why)); return
            end
            job.connected=true
            job.output=job.stage=='port' and ('\0\0\0\15RemoteDBServer\0') or Tracks.number(1).wire
            job.sent=0
        end
        if job.sent<#job.output then
            local sent,why,partial=job.socket:send(job.output,job.sent+1)
            job.sent=sent or partial or job.sent
            assert(sent or why=='timeout','TCP send: '..tostring(why))
            if job.sent<#job.output then return end
        end
        local data,why,partial=job.socket:receive(16384)
        job.buffer=job.buffer..(data or partial or '')
        assert(#job.buffer<=LIMIT+65536,'Metadata buffer limit exceeded')
        if job.stage=='port' then
            if #job.buffer<2 then assert(why=='timeout','Metadata port query closed'); return end
            local port=uint(job.buffer,0,2); assert(port>0 and port<65535,'Metadata server not ready')
            self:open(job.ip,port,'greeting',t); return
        elseif job.stage=='greeting' then
            if #job.buffer<5 then assert(why=='timeout','Metadata greeting closed'); return end
            assert(job.buffer:sub(1,5)==Tracks.number(1).wire,'Invalid metadata greeting')
            job.buffer=job.buffer:sub(6); job.output=Tracks.message(0xfffffffe,0,{Tracks.number(job.asking)}); job.sent=0
            job.stage='setup'; job.deadline=t+3; return
        end
        for _=1,8 do
            local msg,used=Tracks.readMessage(job.buffer)
            if not msg then break end
            job.buffer=job.buffer:sub(used+1)
            local stage=job.stage; self:response(msg,t)
            if not self.job or job.stage~=stage then return end
        end
        assert(data or why=='timeout' or (#job.buffer==0 and job.stage~=self.job.stage),'Metadata connection closed')
    end
    function self:poll(t,engine)
        if self.job then
            local job=self.job; local p=engine.players[job.player]
            local target=engine.players[job.target]
            if not p or not engine:connected(p,t) or identity(p)~=job.key or not engine:connected(target,t) or target.ip~=job.ip then self:stop(); return end
            local ok,err=pcall(self.work,self,t); if not ok then self:fail('Track data: '..tostring(err),t) end
            return
        end
        if options.test or not options.enabled then return end
        for offset=1,6 do
            local id=(self.cursor+offset-1)%6+1; local p=engine.players[id]; local track=self.players[id]
            if track and not track.done and engine:connected(p,t) and p.mode~=0 and p.mode~=2 and
               track.trackId and track.trackId>0 and track.source and track.source>=1 and track.source<=6 and
               t>=(self.nextTry[track.key] or 0) then
                local target=engine.players[track.source]
                if not engine:connected(target,t) then
                    track.status='Waiting for source player'; self.nextTry[track.key]=t+1
                elseif not options.observer and not (target.name=='CDJ-3000 SIM' and target.ip:match('^127%.')) then
                    track.status='Enable observer announcements for metadata'; self.nextTry[track.key]=t+1
                elseif not (target.name or ''):match('^CDJ%-3000') then
                    track.status='Metadata currently supports CDJ-3000 source players'; self.nextTry[track.key]=t+15
                else
                    self.cursor=id; self.job={player=id,target=track.source,key=track.key,slot=track.slot,
                        trackType=track.trackType,trackId=track.trackId,asking=track.source,tx=0}
                    track.status='Loading track metadata'; track.attempts=(track.attempts or 0)+1; self.status=track.status
                    local ok,err=pcall(self.open,self,target.ip,options.queryPort or 12523,'port',t)
                    if not ok then self:fail('Track data: '..tostring(err),t) end
                    return
                end
            end
        end
    end
    return self
end
return Tracks
