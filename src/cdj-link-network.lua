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
