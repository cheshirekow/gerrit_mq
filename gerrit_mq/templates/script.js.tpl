// If the stream is stalled waiting for the log to flush, then wait this
// long before trying again.
var kPollPeriod = 5000;

// NOTE(josh): set to something like 100 to test streaming of large
// uncompressed logs.
var kMaxIncrementSize = 512 * 1024;

// if a <pre> contains more than this many lines, then split it out and create
// a new <pre>
var kLinesPerElement = 100;

// Base URL for gerrit links
var kGerritURL = "https://localhost:8443";

// Number of (history or queue) items to show per page by default
var kDefaultItemsPerPage = 50;

// Parse the URI query string and return a json object mapping query keys
// to their values. Also sets certain keys to their default values if they
// are not in the URI query.
function get_query_as_object() {
  var query_str = location.search.substring(1);
  var query_obj = {};
  query_str.replace(/([^=&]+)=([^&]*)/g, function(m, key, value) {
      query_obj[decodeURIComponent(key)] = decodeURIComponent(value);
  });

  if('page' in query_obj) {
    query_obj.page = parseInt(query_obj.page);
  } else {
    query_obj.page = 0;
  }

  if('page_size' in query_obj) {
    query_obj.page_size = parseInt(query_obj.page_size);
  } else {
    query_obj.page_size = kDefaultItemsPerPage;
  }

  if('merge_id' in query_obj) {
    query_obj.merge_id = parseInt(query_obj.merge_id);
  } else {
    query_obj.merge_id = -1;
  }

  if('follow_stream' in query_obj) {
    query_obj.follow_stream = (query_obj.follow_stream == 'true');
  } else {
    query_obj.follow_stream = true;
  }

  return query_obj;
}

// Set pager link URLs based on pager setup.
function set_pagination(current_page, total_pages, page_size) {
  var prev_page = 0;
  if(current_page > 0){
    prev_page = current_page - 1;
  }

  var next_page = current_page + 1;
  var last_page = next_page;

  if(total_pages !== null) {
    last_page = (total_pages -1);
    if(next_page > last_page) {
      next_page = last_page;
    }
  }

  document.getElementById('first_page_anchor').href =
    "?page=0&page_size=" + page_size;
  document.getElementById('prev_page_anchor').href =
    "?page=" + prev_page + "&page_size=" + page_size;
  document.getElementById('next_page_anchor').href =
    "?page=" + next_page + "&page_size=" + page_size;
  document.getElementById('last_page_anchor').href =
    "?page=" + last_page + "&page_size=" + page_size;
  document.getElementById('page_input').value =
    "" + current_page;
  document.getElementById('page_size_input').value =
    "" + page_size;
}


// find the kLinesPerElement'th occurance of a newline character and return
// it's index, if found, or -1
function get_split_index(text) {
  var split_index = text.indexOf('\n', 0);
  for(var delim_count = 0; delim_count < kLinesPerElement; delim_count++) {
    split_index = text.indexOf('\n', split_index + 1);
    if(split_index == -1) {
      return -1;
    }
  }

  return split_index;
}

// Add a block of text to the current stream, one "page" at a time. This
// greatly improves page responsiveness over appending directly to a single
// div, in which case the rendering engine must reflow a giant continuous
// block of text every time.
function add_text_to_stream(ctx, text, on_complete, timeout) {
  var total_text = ctx.element.innerHTML + text;

  // NOTE(josh): javascript split() doesn't work likepython split() and
  // if we specify a limit then we discard the entire string after that
  // many delimiters.
  var split_index = get_split_index(text);
  if(split_index == -1) {
    ctx.element.innerHTML += text;
    if(page_context.follow_stream
       && (ctx.extension == page_context.active_div)) {
       window.scrollTo(0, document.body.scrollHeight);
    }
    setTimeout(on_complete, timeout);
  } else {
    ctx.element.innerHTML = total_text.slice(0, split_index);
    var remainder = total_text.slice(split_index + 1);
    var old_pre = ctx.element;
    var new_pre = document.createElement('pre');

    old_pre.parentNode.insertBefore(new_pre, old_pre.nextSibling);
    ctx.element = new_pre;

    setTimeout(function(){
                 add_text_to_stream(ctx, remainder, on_complete, timeout);
               }, 0);
  }
}



// The log we want has finished streaming and has been gzipped. We must
// fetch the whole thing at once.
function fetch_log_content(ctx) {
  $.ajax({
    type: 'GET',
    url: '/logs/' + ctx.numstr + '.' + ctx.extension,
    success: function(text, text_status, jqXHR) {
      add_text_to_stream(ctx, text);
    },
    // TODO(josh): add failure to backoff and try again
    dataType: 'text',
  });
}

// Fetch the next incremental bit of the log
function fetch_log_increment(ctx) {
  var bytes_requested = ctx.bytes_total - ctx.bytes_received;
  if(bytes_requested > kMaxIncrementSize) {
    bytes_requested = kMaxIncrementSize;
  }

  $.ajax({
    type: 'GET',
    url: '/logs/' + ctx.numstr + '.' + ctx.extension,
    headers: {
      'Range' : 'bytes=' + ctx.bytes_received + '-'
                + (ctx.bytes_received + bytes_requested),
    },
    success: function(text, text_status, jqXHR) {
      ctx.bytes_received +=
        parseInt(jqXHR.getResponseHeader('Content-Length'));

      response_range = jqXHR.getResponseHeader('Content-Range');
      if(response_range === null){
        console.log("Unexpected Content-Range: " + response_range);
        add_text_to_stream(ctx, text, function(){fetch_log_header(ctx);},
                           kPollPeriod);
        return
      }

      response_parts = response_range.split('/');
      if(response_parts.length != 2) {
        console.log("Unexpected Content-Range: " + response_range);
        add_text_to_stream(ctx, text, function(){fetch_log_header(ctx);},
                           kPollPeriod);
        return;
      } else {
        ctx.bytes_total = parseInt(response_parts[1]);
        if(ctx.bytes_received == ctx.bytes_total) {
          // There is no more data available to be read, go back to
          // fetching just the header until there is more data to read
          add_text_to_stream(ctx, text, function(){fetch_log_header(ctx);}, 0);
        } else {
          add_text_to_stream(ctx, text,
                             function(){fetch_log_increment(ctx);}, 0);
        }
      }
    },
    // TODO(josh): add failure to backoff and try again
    dataType: 'text',
  });
}

// Initial request just fetches headers which tells us wether or not the
// file is currently streaming or if it's already completed and
// compressed (in which case we have to fetch the whole thing at once).
function fetch_log_header(ctx) {
  var xhttp = new XMLHttpRequest();
  xhttp.onreadystatechange = function() {
    // TODO(josh): implement error handler!!
    if (this.readyState == 4 && this.status == 200) {
      accept_ranges = this.getResponseHeader('Accept-Ranges');
      content_length = this.getResponseHeader('Content-Length');

      if(accept_ranges == 'bytes'){
        // console.log('Server accepts byte ranges');
      } else {
        // TODO(josh): server will not accept byte ranges if file is too small,
        // so look at Content-Encoding to tell whether or not the transfer was
        // gzipped. If it was gzipped, then we are done. If not, then keep
        // polling.
        console.log('Server does not accept byte ranges: '
                    + accept_ranges + ', assuming complete gzipped log');
        console.log(this.getAllResponseHeaders());
        console.log(this.url);
        fetch_log_content(ctx);
        return;
      }

      if(content_length === null) {
        console.log('No content-length header!!');
        console.log(this.getAllResponseHeaders());
        console.log(this.url);
        return;
      } else {
        ctx.bytes_total = content_length;

        if(ctx.bytes_total > ctx.bytes_received) {
          // There are new bytes available to read, so start fetching
          // them.
          fetch_log_increment(ctx);
        } else {
          // We have read everything there is to read, so fetch the
          // headers again after one second to see if new data is
          // available.
          setTimeout(function(){
            fetch_log_header(ctx);
          }, kPollPeriod);
        }
      }
    }
  }
  xhttp.open('HEAD', '/logs/' + ctx.numstr + '.' + ctx.extension);
  xhttp.setRequestHeader('Accept', 'application/octet-stream');
  xhttp.send();
}

// Initialize a log stream context and start the fetch state machine.
function init_log_stream(numstr, extension) {
  var stream_dl = document.getElementById(extension + '_dl');
  stream_dl.href = "logs/" + numstr + "." + extension;
  stream_dl.style.display = "inline";

  var stream_pre = document.getElementById(extension + '_pre');
  context = {
    numstr : numstr,
    extension : extension,
    element : stream_pre,
    bytes_received : 0,
    bytes_total : 0,
  }

  fetch_log_header(context);
  return context;
}


// Show a div and hide the rest
function show_div(event, div_name) {
  page_context.active_div = div_name;
  if(event !== null){
    event.preventDefault();
  }
  var div_names = ["log", "stdout", "stderr"]
  for(i=0; i < 3; i++) {
    if(div_name == div_names[i]) {
      document.getElementById(div_names[i] + "_div")
        .style.display = "block";
    } else {
      document.getElementById(div_names[i] + "_div")
        .style.display = "none";
    }
  }
  return false;
}


// Enable/Disable stream following
function set_follow(event, value) {
  if(event != null) {
    event.preventDefault();
  }

  page_context.follow_stream = value;
  if(value) {
    window.scrollTo(0, document.body.scrollHeight);
  }
}

function cancel_merge(event, merge_id) {
  if(event != null) {
    event.preventDefault();
  }

  $.ajax({
    url: "/gmq/cancel_merge?rid=" + merge_id,
    success: function(response, text_status, jqXHR){
      console.log("Merge cancelation result " + response);
      event.target.parentNode.removeChild(event.target);
    },
    dataType: 'text',
  });
}

function parse_datetime(date_str) {
  datetime_parts = date_str.split(' ');
  date = datetime_parts[0];
  time = datetime_parts[1];

  date_parts = date.split('-');
  time_parts = time.split(':');

  date = Date.UTC(parseInt(date_parts[0]),
                  parseInt(date_parts[1]) - 1,
                  parseInt(date_parts[2]),
                  parseInt(time_parts[0]),
                  parseInt(time_parts[1]),
                  parseInt(time_parts[2]));
  return date;
}

// Start an async request to fetch details and then render them with Mustache.
function fetch_details(merge_id) {
  if(merge_id == -1) {
    query_url = "/gmq/get_merge_status"
  } else {
    query_url = "/gmq/get_merge_status?rid=" + merge_id
  }

  $.ajax({
    url: query_url,
    success: function(merge_status, text_status, jqXHR){
      console.log("Data received");

      merge_status.gerrit_url = kGerritURL
      compute_merge_durations(merge_status);

      if('metadata' in merge_status
        && 'Feature-Branch' in merge_status.metadata) {
        merge_status.feature_branch =
          merge_status.metadata['Feature-Branch'];
      } else {
        merge_status.feature_branch = 'null';
      }

      if(merge_status.status == 0) {
        merge_status.result_class = 'success';
      } else if (merge_status.status == 1) {
        merge_status.result_class = 'inprogress';
        var cancel_template = $('#cancel_tpl').html()
        merge_status.cancel_btn = Mustache.to_html(cancel_template,
                                                   merge_status);
      } else {
        merge_status.result_class = 'failure';
      }


      var table_template = $('#table_tpl').html()
      document.getElementById('table_div').innerHTML =
        Mustache.to_html(table_template, merge_status);

      numstr = "" + merge_status.rid;
      while(numstr.length < 6) {
        numstr = "0" + numstr;
      }

      log_ctx = init_log_stream(numstr, 'log');
      stdout_ctx = init_log_stream(numstr, 'stdout');
      stderr_ctx = init_log_stream(numstr, 'stderr');

      if(merge_status.status == 1) {
        var request_time = parse_datetime(merge_status.request_time) / 1000;
        var start_time = parse_datetime(merge_status.start_time) / 1000;

        var merge_duration_cell = document.getElementById('merge_duration')
        var total_duration_cell = document.getElementById('total_duration')
        setInterval(function(){
                      var end_time = Date.now() / 1000;
                      merge_duration_cell.innerHTML =
                         format_duration(end_time - start_time);
                      total_duration_cell.innerHTML =
                        format_duration(end_time - request_time);
                    }, 1000);
      }
    },
    dataType: 'json',
  });
}


function format_duration(duration_seconds) {
    var hours   = Math.floor(duration_seconds / 3600);
    var minutes = Math.floor((duration_seconds - (hours * 3600)) / 60);
    var seconds = duration_seconds - (hours * 3600) - (minutes * 60);
    seconds = Math.floor(seconds * 100) / 100;

    if (hours   < 10) {hours   = "0"+hours;}
    if (minutes < 10) {minutes = "0"+minutes;}
    if (seconds < 10) {seconds = "0"+seconds;}

    if(hours != "00") {
      return hours+"h"+minutes+"m"+seconds+"s";
    } else if(minutes != "00") {
      return minutes+"m"+seconds+"s";
    } else {
      return seconds+"s";
    }

}

// Ugly mutator. Parses request_time, start_time, and end_time strings in a
// merge_status object, then computes durations, then stores those durations
// as strings
// TODO(josh): compute all derived fields, including feature_branch,
// and result_class
function compute_merge_durations(merge_status) {
  request_time = Date.parse(merge_status.request_time) / 1000;
  start_time = Date.parse(merge_status.start_time) / 1000;
  end_time = Date.parse(merge_status.end_time) / 1000;

  queue_duration = start_time - request_time;
  merge_duration = end_time - start_time;
  total_duration = end_time - request_time;

  merge_status.queue_duration = format_duration(queue_duration);
  merge_status.merge_duration = format_duration(merge_duration);
  merge_status.total_duration = format_duration(total_duration);
}


function update_build_duration(start_time, cell) {
  cell.innerHTML = format_duration(Date().getTime() - start_time);
}


// Callback for when history page data is received from webfront
function render_history(query_obj, data) {
  var num_pages = Math.ceil(data.count / query_obj.page_size);
  set_pagination(query_obj.page, num_pages, query_obj.page_size);

  var template = $('#row_tpl').html()
  for(idx in data.result) {
    merge_status = data.result[idx];
    if ('Feature-Branch' in merge_status.metadata){
      merge_status.feature_branch =
        merge_status.metadata['Feature-Branch'];
    } else {
      merge_status.feature_branch = 'null';
    }
    var new_row = document.getElementById('history_table')
                  .insertRow(-1);

    merge_status.gerrit_url = kGerritURL;
    compute_merge_durations(merge_status);

    new_row.innerHTML = Mustache.to_html(template, merge_status);
    if(merge_status.status == 0) {
      new_row.className += " success";
    } else if(merge_status.status == 1) {
      new_row.className += " inprogress";
    } else {
      new_row.className += " failure";
    }

    if(query_obj.page == 0 && idx == 0 && merge_status.status == 1) {
      var start_time = parse_datetime(merge_status.start_time) / 1000;
      var duration_cell = new_row.cells[new_row.cells.length-1];
      setInterval(function(){
                    var end_time = Date.now() / 1000;
                    duration_cell.innerHTML =
                      format_duration(end_time - start_time);
                  }, 1000);
    }
  }
}

// Called when the history page is ready
function history_page_ready() {
  var query_obj = get_query_as_object();

  set_pagination(query_obj.page, null, query_obj.page_size);
  console.log("Document ready, fetching data");
  $.ajax({
    url: "/gmq/get_history?offset=" + (query_obj.page
                                       * query_obj.page_size)
         + "&limit=" + query_obj.page_size,
    data: null,
    success: function(data, text_status, jqXHR){
      console.log("Data received, total count: " + data.count);
      render_history(query_obj, data);
    },
    dataType: 'json',
  });
}


// Callback for when current queue data is received from webfront
function render_queue(query_obj, data) {
  var num_pages = Math.ceil(data.count / query_obj.page_size);
  set_pagination(query_obj.page, num_pages, query_obj.page_size);

  var template = $('#row_tpl').html()
  for(idx in data.result) {
    change_info = data.result[idx];
    change_info.queue_index = idx;
    if ('Feature-Branch' in change_info.message_meta){
      change_info.feature_branch =
        change_info.message_meta['Feature-Branch'];
    } else {
      change_info.feature_branch = 'null';
    }
    var new_row = document.getElementById('history_table')
                  .insertRow(-1);
    change_info.gerrit_url = kGerritURL
    new_row.innerHTML = Mustache.to_html(template, change_info);
  }
}

// Called when the queue page is ready
function queue_page_ready() {
  var query_obj = get_query_as_object();

  set_pagination(query_obj.page, null, query_obj.page_size);
  console.log("Document ready, fetching data");
  $.ajax({
    url: "/gmq/get_queue?offset=" + (query_obj.page
                                       * query_obj.page_size)
         + "&limit=" + query_obj.page_size,
    data: null,
    success: function(data, text_status, jqXHR){
      console.log("Data received, total count: " + data.count);
      render_queue(query_obj, data);
    },
    dataType: 'json',
  });
}



function set_pause_view(elems, value) {
  if(value) {
    elems.paused.className = "inprogress";
    elems.paused.innerHTML = value;
    elems.pause_button.innerHTML = "[resume]";
    elems.pause_button.onclick = function(event){
      pause_daemon(event, false);
      return false;
    }
  } else {
    elems.paused.className = "success";
    elems.paused.innerHTML = value;
    elems.pause_button.innerHTML = "[pause]";
    elems.pause_button.onclick = function(event){
      pause_daemon(event, true);
      return false;
    }
  }
}

function handle_daemon_status(elems, data) {
  elems.alive.innerHTML = data.alive;
  if(data.alive) {
    elems.alive.className = "success";
  } else {
    elems.alive.className = "failure";
  }

  set_pause_view(elems, data.paused);
  elems.pid.innerHTML = data.pid;
}

function pause_daemon(event, value) {
  var elems = {
    alive : document.getElementById('alive_cell'),
    paused : document.getElementById('paused_cell'),
    pid : document.getElementById('pid_cell'),
    pause_button : document.getElementById('pause_button'),
  }

  if(event !== null) {
    event.preventDefault();
  }

  $.ajax({
    url: "/gmq/set_daemon_pause?value=" + value,
    data: null,
    success: function(data, text_status, jqXHR){
      console.log('status received');
      handle_daemon_status(elems, data);
    },
    dataType: 'json',
  });
}

function query_daemon_status() {
  var elems = {
    alive : document.getElementById('alive_cell'),
    paused : document.getElementById('paused_cell'),
    pid : document.getElementById('pid_cell'),
    pause_button : document.getElementById('pause_button'),
  }

  $.ajax({
    url: "/gmq/get_daemon_status",
    data: null,
    success: function(data, text_status, jqXHR){
      handle_daemon_status(elems, data);
    },
    dataType: 'json',
  });
}

// Called when the daemon page is ready
function daemon_page_ready() {
  query_daemon_status();
  setInterval(query_daemon_status, 10000)
}
