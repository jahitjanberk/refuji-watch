// ── Vector basemap ────────────────────────────────────────────────────────
// Draws country outlines from the self-hosted world.json instead of raster
// tiles. Replaces CARTO's basemaps, which now stamp "API KEY REQUIRED" over
// keyless requests. No API key, no tile host, no usage quota.
//
// Usage: addBasemap(leafletMap)   — safe to call for several maps; the
// GeoJSON is fetched once and shared.
(function(){
  var _geo = null;   // cached fetch promise

  function loadWorld(){
    if(!_geo){
      _geo = fetch('/world.json').then(function(r){
        if(!r.ok) throw new Error('world.json: HTTP '+r.status);
        return r.json();
      });
    }
    return _geo;
  }

  window.addBasemap = function(map, opts){
    opts = opts || {};
    var pane = map.createPane('basemap');
    pane.style.zIndex = 200;              // beneath markers, above the container
    return loadWorld().then(function(geo){
      L.geoJSON(geo,{
        pane:'basemap',
        interactive:false,
        style:{
          fillColor: opts.fill   || '#141414',
          color:     opts.stroke || '#2a2a2a',
          weight:    opts.weight || 0.5,
          fillOpacity: 1
        }
      }).addTo(map);
      return map;
    }).catch(function(e){ console.warn('Basemap unavailable:', e); });
  };
})();
