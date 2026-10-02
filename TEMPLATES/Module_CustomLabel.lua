local p = {}

function p.label_Q(frame)
    local args = frame.args
    local qid = args[1]

    if not qid or qid == "" then
        qid = frame:getParent().args[1]
    end

    if not qid or qid == "" or qid == "Qx" then return "" end

    qid = mw.text.trim(qid)
    local label = mw.wikibase.getLabel(qid)
    if not label or label == "" then label = mw.wikibase.getLabelByLang(qid, 'es') end
    if not label or label == "" then label = mw.wikibase.getLabelByLang(qid, 'en') end

    return label or qid
end

function p.label_Q_desc(frame)
    local args = frame.args
    local qid = args[1]
    if not qid or qid == "" then qid = frame:getParent().args[1] end
    if not qid or qid == "" or qid == "Qx" then return "" end

    qid = mw.text.trim(qid)
    local label = mw.wikibase.getLabel(qid)
    if not label or label == "" then label = mw.wikibase.getLabelByLang(qid, 'es') end
    if not label or label == "" then label = mw.wikibase.getLabelByLang(qid, 'en') end
    local texto_visible = label or qid

    local desc = mw.wikibase.getDescription(qid)
    if not desc or desc == "" then desc = mw.wikibase.getDescriptionByLang(qid, 'es') end
    if not desc or desc == "" then desc = mw.wikibase.getDescriptionByLang(qid, 'en') end

    if desc and desc ~= "" then
        return texto_visible .. "<br><small>" .. desc .. "</small>"
    else
        return texto_visible
    end
end

function p.label_Qlnk(frame)
    local args = frame.args
    local qid = args[1]

    if not qid or qid == "" then
        qid = frame:getParent().args[1]
    end

    -- Si sigue sin haber QID, devolvemos vacío para no romper la plantilla
    if not qid or qid == "" or qid == "Qx" then
        return ""
    end

    qid = mw.text.trim(qid)

    -- 1. Intentar obtener el label en el idioma de la wiki
    local label = mw.wikibase.getLabel(qid)

    -- 2. Si falla, forzamos español ('es')
    if not label or label == "" then
        label = mw.wikibase.getLabelByLang(qid, 'es')
    end

    -- 3. Si sigue fallando, probamos inglés ('en')
    if not label or label == "" then
        label = mw.wikibase.getLabelByLang(qid, 'en')
    end

    local texto_visible = label or qid

    return "[[Item:" .. qid .. "|" .. texto_visible .. "]]"
end

function p.label_Qlnk_desc(frame)
    local args = frame.args
    local qid = args[1]
    if not qid or qid == "" then qid = frame:getParent().args[1] end
    if not qid or qid == "" or qid == "Qx" then return "" end

    qid = mw.text.trim(qid)
    local label = mw.wikibase.getLabel(qid)
    if not label or label == "" then label = mw.wikibase.getLabelByLang(qid, 'es') end
    if not label or label == "" then label = mw.wikibase.getLabelByLang(qid, 'en') end
    local texto_visible = label or qid

    local desc = mw.wikibase.getDescription(qid)
    if not desc or desc == "" then desc = mw.wikibase.getDescriptionByLang(qid, 'es') end
    if not desc or desc == "" then desc = mw.wikibase.getDescriptionByLang(qid, 'en') end

    local enlace = "[[Item:" .. qid .. "|" .. texto_visible .. "]]"
    if desc and desc ~= "" then
        return enlace .. "<br><small>" .. desc .. "</small>"
    else
        return enlace
    end
end

function p.propiedad_Qlnk(frame)
    local args = frame.args
    local qid = args[1]
    local prop = args[2]
            
    if not qid or qid == "" then qid = frame:getParent().args[1] end
    if not prop or prop == "" then prop = frame:getParent().args[2] end
            
    if not qid or qid == "" or qid == "Qx" then return "" end
    if not prop or prop == "" then return "" end
            
    qid = mw.text.trim(qid)
    prop = mw.text.trim(prop)
            
    local statements = mw.wikibase.getBestStatements(qid, prop)
    if not statements or #statements == 0 then return "" end
            
    local stmt = statements[1]
    if not stmt.mainsnak or not stmt.mainsnak.datavalue then return "" end
            
    local datavalue = stmt.mainsnak.datavalue
    local texto_resultado = ""
    local desc = ""

    -- CASO 1: Si es una entidad referenciada
    if datavalue.type == "wikibase-entityid" then
        local raw_id = tostring(datavalue.value.id)
        local target_qid = string.upper(raw_id)
        if not target_qid:find("^Q") then
            target_qid = "Q" .. target_qid
        end

        local label = mw.wikibase.getLabel(target_qid)
        if not label or label == "" then label = mw.wikibase.getLabelByLang(target_qid, 'es') end
        if not label or label == "" then label = mw.wikibase.getLabelByLang(target_qid, 'en') end
        local texto_visible = label or target_qid
        
        texto_resultado = "[[Item:" .. target_qid .. "|" .. texto_visible .. "]] (Item:" .. target_qid .. ")"

        desc = mw.wikibase.getDescription(target_qid)
        if not desc or desc == "" then desc = mw.wikibase.getDescriptionByLang(target_qid, 'es') end
        if not desc or desc == "" then desc = mw.wikibase.getDescriptionByLang(target_qid, 'en') end

    -- CASO 2: Si es un texto plano u otro tipo de valor (enlazado a su Q principal)
    else
        local valor_plano = ""
        if datavalue.type == "string" then
            valor_plano = tostring(datavalue.value)
        elseif datavalue.type == "monolingualtext" then
            valor_plano = tostring(datavalue.value.text)
        else
            valor_plano = tostring(datavalue.value)
        end

        -- Enlazamos el texto plano al Q principal consultado
        texto_resultado = "[[Item:" .. qid .. "|" .. valor_plano .. "]]"

        desc = mw.wikibase.getDescription(prop)
        if not desc or desc == "" then desc = mw.wikibase.getDescriptionByLang(prop, 'es') end
        if not desc or desc == "" then desc = mw.wikibase.getDescriptionByLang(prop, 'en') end
    end
            
    if texto_resultado == "" then return "" end

    if desc and desc ~= "" then
        return texto_resultado .. "<br><small>" .. desc .. "</small>"
    else
        return texto_resultado
    end
end

-- ===========================================================================
-- propiedad_Qlnk_desc_prop: como propiedad_Qlnk, pero para el CASO 1 (valor
-- de tipo entidad/wikibase-item) la línea inferior muestra SIEMPRE la
-- descripción de la PROPIEDAD consultada (su definición RiC-CM/ISAD(G), la
-- misma fuente que usa ya el CASO 2 para campos de texto como P_titulo), en
-- vez de la descripción del ítem enlazado. Pensada para P_productor: la
-- descripción del ítem Agente es una paráfrasis de su propio label ("Agente
-- productor de la documentación (X) (rico:CorporateBody)", ver
-- obtener_o_crear_productor en 020_cargar_cuadro_clasificacion.py) y no
-- aporta nada nuevo, mientras que la descripción de la propiedad P_productor
-- ("<rico:hasOrHadCreator> Agente responsable de la producción del
-- documento...") sí explica el campo — igual que ocurre con P_titulo. No se
-- usa para propiedades donde la descripción del ítem destino SÍ es
-- informativa por sí misma (p. ej. P_fecha_agrupacion, cuya descripción da
-- la fecha en sí).
-- Parámetros: 1=QID, 2=PID
-- ===========================================================================
function p.propiedad_Qlnk_desc_prop(frame)
    local args = frame.args
    local qid = args[1]
    local prop = args[2]

    if not qid or qid == "" then qid = frame:getParent().args[1] end
    if not prop or prop == "" then prop = frame:getParent().args[2] end

    if not qid or qid == "" or qid == "Qx" then return "" end
    if not prop or prop == "" then return "" end

    qid = mw.text.trim(qid)
    prop = mw.text.trim(prop)

    local statements = mw.wikibase.getBestStatements(qid, prop)
    if not statements or #statements == 0 then return "" end

    local stmt = statements[1]
    if not stmt.mainsnak or not stmt.mainsnak.datavalue then return "" end

    local datavalue = stmt.mainsnak.datavalue
    if datavalue.type ~= "wikibase-entityid" then return "" end

    local raw_id = tostring(datavalue.value.id)
    local target_qid = string.upper(raw_id)
    if not target_qid:find("^Q") then
        target_qid = "Q" .. target_qid
    end

    local label = mw.wikibase.getLabel(target_qid)
    if not label or label == "" then label = mw.wikibase.getLabelByLang(target_qid, 'es') end
    if not label or label == "" then label = mw.wikibase.getLabelByLang(target_qid, 'en') end
    local texto_visible = label or target_qid

    local texto_resultado = "[[Item:" .. target_qid .. "|" .. texto_visible .. "]] (Item:" .. target_qid .. ")"

    local desc = mw.wikibase.getDescription(prop)
    if not desc or desc == "" then desc = mw.wikibase.getDescriptionByLang(prop, 'es') end
    if not desc or desc == "" then desc = mw.wikibase.getDescriptionByLang(prop, 'en') end

    if desc and desc ~= "" then
        return texto_resultado .. "<br><small>" .. desc .. "</small>"
    else
        return texto_resultado
    end
end

function p.propiedad_derivada_Qlnk(frame)
    local args = frame.args
    local qid = args[1]
    local prop_origen = args[2]
    local prop_destino = args[3]

    if not qid or qid == "" then qid = frame:getParent().args[1] end
    if not prop_origen or prop_origen == "" then prop_origen = frame:getParent().args[2] end
    if not prop_destino or prop_destino == "" then prop_destino = frame:getParent().args[3] end

    if not qid or qid == "" or qid == "Qx" then return "" end
    if not prop_origen or prop_origen == "" then return "" end
    if not prop_destino or prop_destino == "" then return "" end

    qid = mw.text.trim(qid)
    prop_origen = mw.text.trim(prop_origen)
    prop_destino = mw.text.trim(prop_destino)

    -- PASO 1: Buscar la propiedad origen para obtener el Q destino
    local statements_origen = mw.wikibase.getBestStatements(qid, prop_origen)
    if not statements_origen or #statements_origen == 0 then return "" end

    local stmt_origen = statements_origen[1]
    if not stmt_origen.mainsnak or not stmt_origen.mainsnak.datavalue then return "" end

    local datavalue_origen = stmt_origen.mainsnak.datavalue
    if datavalue_origen.type ~= "wikibase-entityid" then return "" end

    local raw_id_destino = tostring(datavalue_origen.value.id)
    local q_destino = string.upper(raw_id_destino)
    if not q_destino:find("^Q") then
        q_destino = "Q" .. q_destino
    end

    if q_destino == "" or q_destino == "Qx" then return "" end

    -- PASO 2: Buscar la propiedad destino en el Q destino encontrado
    local statements_destino = mw.wikibase.getBestStatements(q_destino, prop_destino)
    if not statements_destino or #statements_destino == 0 then return "" end

    local stmt_destino = statements_destino[1]
    if not stmt_destino.mainsnak or not stmt_destino.mainsnak.datavalue then return "" end

    local datavalue_destino = stmt_destino.mainsnak.datavalue
    local texto_resultado = ""

    -- PASO 3: Procesar según el tipo de valor asegurando el enlace al Q correspondiente
    if datavalue_destino.type == "wikibase-entityid" then
        local raw_id_final = tostring(datavalue_destino.value.id)
        local target_qid = string.upper(raw_id_final)
        if not target_qid:find("^Q") then
            target_qid = "Q" .. target_qid
        end

        local label = mw.wikibase.getLabel(target_qid)
        if not label or label == "" then label = mw.wikibase.getLabelByLang(target_qid, 'es') end
        if not label or label == "" then label = mw.wikibase.getLabelByLang(target_qid, 'en') end
        local texto_visible = label or target_qid

        texto_resultado = "[[Item:" .. target_qid .. "|" .. texto_visible .. "]]"
    else
        local valor_plano = ""
        if datavalue_destino.type == "string" then
            valor_plano = tostring(datavalue_destino.value)
        elseif datavalue_destino.type == "monolingualtext" then
            valor_plano = tostring(datavalue_destino.value.text)
        else
            valor_plano = tostring(datavalue_destino.value)
        end

        -- Enlazamos el texto plano al q_destino donde reside la propiedad destino
        texto_resultado = "[[Item:" .. q_destino .. "|" .. valor_plano .. "]]"
    end

    if texto_resultado == "" then return "" end

    -- Se anota siempre: este valor no vive en el item principal consultado,
    -- sino en la propiedad prop_destino del item q_destino (p. ej. el
    -- volumen/soporte vive en la instanciación física, no en el documento).
    texto_resultado = texto_resultado .. " (Propiedad " .. prop_destino .. " del Item:" .. q_destino .. ")"

    -- Descripción de la definición de la propiedad destino
    local desc = mw.wikibase.getDescription(prop_destino)
    if not desc or desc == "" then desc = mw.wikibase.getDescriptionByLang(prop_destino, 'es') end
    if not desc or desc == "" then desc = mw.wikibase.getDescriptionByLang(prop_destino, 'en') end

    if desc and desc ~= "" then
        return texto_resultado .. "<br><small>" .. desc .. "</small>"
    else
        return texto_resultado
    end
end

-- ===========================================================================
-- Helper interno: convierte un único snak (mainsnak) en texto renderizable
-- (enlace [[Item:...]] si es una entidad, texto plano si es otro tipo).
-- Reutilizado por las funciones _multi para no duplicar el CASO 1 / CASO 2
-- que ya usan propiedad_Qlnk y propiedad_derivada_Qlnk para un único valor.
-- ===========================================================================
local function renderizar_valor_snak(datavalue, anotar_item)
    if not datavalue then return "" end

    if datavalue.type == "wikibase-entityid" then
        local raw_id = tostring(datavalue.value.id)
        local target_qid = string.upper(raw_id)
        if not target_qid:find("^Q") then
            target_qid = "Q" .. target_qid
        end

        local label = mw.wikibase.getLabel(target_qid)
        if not label or label == "" then label = mw.wikibase.getLabelByLang(target_qid, 'es') end
        if not label or label == "" then label = mw.wikibase.getLabelByLang(target_qid, 'en') end
        local texto_visible = label or target_qid

        local resultado = "[[Item:" .. target_qid .. "|" .. texto_visible .. "]]"
        if anotar_item then
            resultado = resultado .. " (Item:" .. target_qid .. ")"
        end
        return resultado
    elseif datavalue.type == "string" then
        return tostring(datavalue.value)
    elseif datavalue.type == "monolingualtext" then
        return tostring(datavalue.value.text)
    else
        return tostring(datavalue.value)
    end
end

-- ===========================================================================
-- propiedad_Qlnk_multi: como propiedad_Qlnk, pero muestra TODOS los valores
-- de una propiedad repetible (p. ej. P_lengua con Castellano y Valenciano a
-- la vez), no solo el primero. No añade la descripción de cada valor (con
-- varios resultados quedaría demasiado cargado) — solo la lista de valores.
-- Parámetros: 1=QID, 2=PID, 3=separador (opcional, por defecto ", ")
-- ===========================================================================
function p.propiedad_Qlnk_multi(frame)
    local args = frame.args
    local qid = args[1]
    local prop = args[2]
    local separador = args[3]

    if not qid or qid == "" then qid = frame:getParent().args[1] end
    if not prop or prop == "" then prop = frame:getParent().args[2] end
    if not separador or separador == "" then separador = frame:getParent().args[3] end
    if not separador or separador == "" then separador = ", " end

    if not qid or qid == "" or qid == "Qx" then return "" end
    if not prop or prop == "" then return "" end

    qid = mw.text.trim(qid)
    prop = mw.text.trim(prop)

    local statements = mw.wikibase.getBestStatements(qid, prop)
    if not statements or #statements == 0 then return "" end

    local partes = {}
    for _, stmt in ipairs(statements) do
        if stmt.mainsnak and stmt.mainsnak.datavalue then
            local texto_item = renderizar_valor_snak(stmt.mainsnak.datavalue, true)
            if texto_item ~= "" then
                table.insert(partes, texto_item)
            end
        end
    end

    if #partes == 0 then return "" end
    return table.concat(partes, separador)
end

-- ===========================================================================
-- propiedad_derivada_Qlnk_multi: como propiedad_derivada_Qlnk (salto en dos
-- pasos: qid -prop_origen-> q_destino -prop_destino-> valores), pero muestra
-- TODOS los valores de la propiedad destino (p. ej. P_tipo_escritura con
-- Bastarda, Híbrida y Procesal a la vez sobre una misma instanciación física).
-- Parámetros: 1=QID, 2=PID origen, 3=PID destino, 4=separador (opcional)
-- ===========================================================================
function p.propiedad_derivada_Qlnk_multi(frame)
    local args = frame.args
    local qid = args[1]
    local prop_origen = args[2]
    local prop_destino = args[3]
    local separador = args[4]

    if not qid or qid == "" then qid = frame:getParent().args[1] end
    if not prop_origen or prop_origen == "" then prop_origen = frame:getParent().args[2] end
    if not prop_destino or prop_destino == "" then prop_destino = frame:getParent().args[3] end
    if not separador or separador == "" then separador = frame:getParent().args[4] end
    if not separador or separador == "" then separador = ", " end

    if not qid or qid == "" or qid == "Qx" then return "" end
    if not prop_origen or prop_origen == "" then return "" end
    if not prop_destino or prop_destino == "" then return "" end

    qid = mw.text.trim(qid)
    prop_origen = mw.text.trim(prop_origen)
    prop_destino = mw.text.trim(prop_destino)

    -- PASO 1: igual que en propiedad_derivada_Qlnk — solo el primer valor de
    -- la propiedad origen, ya que se asume una única instanciación física
    -- por documento (si hubiera varias, solo se recorre la primera).
    local statements_origen = mw.wikibase.getBestStatements(qid, prop_origen)
    if not statements_origen or #statements_origen == 0 then return "" end

    local stmt_origen = statements_origen[1]
    if not stmt_origen.mainsnak or not stmt_origen.mainsnak.datavalue then return "" end

    local datavalue_origen = stmt_origen.mainsnak.datavalue
    if datavalue_origen.type ~= "wikibase-entityid" then return "" end

    local raw_id_destino = tostring(datavalue_origen.value.id)
    local q_destino = string.upper(raw_id_destino)
    if not q_destino:find("^Q") then
        q_destino = "Q" .. q_destino
    end
    if q_destino == "" or q_destino == "Qx" then return "" end

    -- PASO 2: aquí sí recorremos TODOS los valores de la propiedad destino
    local statements_destino = mw.wikibase.getBestStatements(q_destino, prop_destino)
    if not statements_destino or #statements_destino == 0 then return "" end

    local partes = {}
    for _, stmt in ipairs(statements_destino) do
        if stmt.mainsnak and stmt.mainsnak.datavalue then
            local texto_item = renderizar_valor_snak(stmt.mainsnak.datavalue, false)
            if texto_item ~= "" then
                table.insert(partes, texto_item)
            end
        end
    end

    if #partes == 0 then return "" end

    -- Se anota una sola vez para todo el conjunto (prop_destino y q_destino
    -- son los mismos para todos los valores devueltos, a diferencia del QID
    -- de destino de cada valor individual, que sí varía).
    local resultado = table.concat(partes, separador)
    return resultado .. " (Propiedad " .. prop_destino .. " del Item:" .. q_destino .. ")"
end

return p