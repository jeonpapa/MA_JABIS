var isDev;
if(window.location.host.indexOf(":") != -1) 
	isDev = true;
else 
	isDev = false;

var Common = {
	isChecked : false,

	// 객체 가져오기
	object : function (id) {
		if (document.getElementById && document.getElementById(id)) {
			return document.getElementById(id);
		} else if (document.getElementByName && document.getElementByName(id)) {
			return document.getElementByName(id);
		} else if (document.all && document.all(id)) {
			return document.all(id);
		} else if (document.layers && document.layers[id]) {
			return document.layers[id];
		} else {
			return false;
		}
	},

	// 체크박스 모두 선택하기
	selAll : function (frm, isObj) {
		if (isObj == true) {
			if (this.isChecked == false) {
				for (var i = 0; i <= frm; i++) {
					if (this.object("_a_" + i + "_").checked == true) {
						continue;
					} else {
						this.object("_a_" + i + "_").checked = true;
					}
				}

				this.isChecked = true;
			} else {
				for (var i = 0; i <= frm; i++) {
					if (this.object("_a_" + i + "_").checked == true) {
						this.object("_a_" + i + "_").checked = false;
					} else {
						continue;
					}
				}

				this.isChecked = false;
			}
			return false;
		} else {
			if (this.isChecked == false) {
				for (i = 0; i < frm.length; i++) {
					if (frm[i].type == "checkbox") {
						if (frm[i].checked == true) {
							continue;
						} else {
							frm[i].checked = true;
						}
					}
				}

				this.isChecked = true;
			} else {
				for (i = 0; i < frm.length; i++) {
					if (frm[i].type == "checkbox") {
						if (frm[i].checked == true) {
							frm[i].checked = false;
						} else {
							continue;
						}
					}
				}

				this.isChecked = false;
			}
		}
	},

	// 새창띄우기
	openWin : function (url, wname, width, height, scrl) {
		var winl = (screen.width - width) / 2;
		var wint = (screen.height - height) / 2;
		//IE인 경우 예외처리
		var agent = navigator.userAgent.toLowerCase();
		if ((navigator.appName === 'Netscape' && agent.search('trident') !== -1) || (agent.indexOf('msie') !== -1)) {
			var check = /[ㄱ-ㅎ|ㅏ-ㅣ|가-힣]/;
			if(check.test(url)) {
				url = url.replace(/[ㄱ-ㅎ|ㅏ-ㅣ|가-힣]/gi,"");
			}
		}
		
		if(url != undefined && url.indexOf("/cms") == 0) {
			url = '/dummy.do?pgmid=HIRAA030035010030&isNewWindow=Y&cmsurl='+url;
		}

		if (typeof scrl == "undefined") {
			var scroll = "yes";
		} else {
			var scroll = scrl;
		}

		return window.open(url, wname, "left=" + winl + ", top=" + wint + ", scrollbars=" + scroll + ", status=yes, resizable=no, width=" + width + ", height=" + height);
	},
	
	// 새창띄우기
	openWinCenter : function (url, wname, width, height, scrl) {
		var winl = Math.round(window.screenX + (window.outerWidth/2) - (width/2));
		var wint = Math.round(window.screenY + (window.outerHeight/2) - (height/2));
		//IE인 경우 예외처리
		var agent = navigator.userAgent.toLowerCase();
		if ((navigator.appName === 'Netscape' && agent.search('trident') !== -1) || (agent.indexOf('msie') !== -1)) {
			var check = /[ㄱ-ㅎ|ㅏ-ㅣ|가-힣]/;
			if(check.test(url)) {
				url = url.replace(/[ㄱ-ㅎ|ㅏ-ㅣ|가-힣]/gi,"");
			}
		}
		
		if(url != undefined && url.indexOf("/cms") == 0) {
			url = '/dummy.do?pgmid=HIRAA030035010030&isNewWindow=Y&cmsurl='+url;
		}

		if (typeof scrl == "undefined") {
			var scroll = "yes";
		} else {
			var scroll = scrl;
		}

		return window.open(url, wname, "left=" + winl + ", top=" + wint + ", scrollbars=" + scroll + ", status=no, resizable=no, width=" + width + ", height=" + height);
	},

	// 포커스 이동
	moveFocus : function (num, fromform, toform) {
// -- 웹접근성 결과에 다른 비활성화
//		var str = fromform.value.length;
//
//		if (str == num) {
//			toform.focus();
//		}
	},

	// 이메일 체크
	isAvailableEmail : function (v) {
		var format = /^((\w|[\-\.])+)@((\w|[\-\.])+)\.([A-Za-z]+)$/;

		if (v.search(format) == -1) {
			return false;
		} else if (v.charAt(v.indexOf('@') + 1) == '.') {
			return false;
		} else {
			return true;
		}
	},

	// 금액에 콤마찍기
	formatNumber : function (n) {
		var str = new String(n);
		var num = str.replace(/\-/gi, "").replace(/,/gi, "").replace(/\./gi, "");
		var sgn = parseInt(num) < 0 || str.substr(0, 1) == "-" ? "-" : "";
		var len = num.length;
		var pos = 3;
		var tmp = "";

		if (isNaN(num)) {
			window.alert("Only number it will be able to input.\n\nInput data = " + num);
			return 0;
		} else if (parseInt(num) == 0) {
			return num;
		}

		while (len > 0) {
			len -= pos;

			if (len < 0) {
				pos = len + pos;
				len = 0;
			}

			tmp = "," + num.substr(len, pos) + tmp;
		}

		return sgn + tmp.substr(1);
	},

	// 문자열 길이
	strLen : function (str) {
		var len = 0;
		var tmp = null;
		var i = 0;

		while (i < str.length) {
			tmp = str.charAt(i);

			if (escape(tmp).length > 4) {
				len += 2;
			} else if (tmp != "\r") {
				len++;
			}

			i++;
		}

		return len;
	},

	// 문자열 자르기
	strCut : function (str, len, tail) {
		if (len == 0 || this.strLen(str) <= len) {
			return str;
		}

		var t = null;
		var i = 0;
		var l = 0;
		var returnValue = "";

		while (i < str.length) {
			t = str.charAt(i);

			if (escape(t).length > 4) {
				l += 2;
			} else if (t != "\r") {
				l += 1;
			}

			returnValue += t;

			if (l >= len) {
				break;
			}

			i++;
		}

		return returnValue + (typeof tail == "undefined" ? "..." : tail);
	},

	// 대문자 -> 소문자
	strToLower : function (str) {
		return str.toLowerCase();
	},

	// 소문자 -> 대문자
	strToUpper : function (str) {
		return str.toUpperCase();
	},

	// 배열안에 값이 있는지 체크
	inArray : function (val, arr) {
		for (var i = 0; i < arr.length; i++) {
			if (arr[i] == val) {
				return true;
			}
		}

		return false;
	},

	// 라디오버튼 체크 여부
	radio : function (frm, act, val) {
		switch (act) {
			// 체크값 구하기
			case 1 :
				if (frm.length > 0) {
					for (var i = 0; i < frm.length; i++) {
						if (frm[i].checked == true) {
							return frm[i].value;
						}
					}
				} else {
					if (frm.checked == true) {
						return frm.value;
					}
				}

				break;

			// 해당 위치에 포커스
			case 2 :
				if (frm.length > 0) {
					for (var i = 0; i < frm.length; i++) {
						if (frm[i].value == val) {
							frm[i].checked = true;
							break;
						}
					}
				} else {
					if (frm.value == val) {
						frm.checked = true;
					}
				}

				break;

			// 체크된 박스의 순번
			case 3 :
				if (frm.length > 0) {
					for (var i = 0; i < frm.length; i++) {
						if (frm[i].value == val) {
							return i;
						}
					}
				} else {
					return 0;
				}

				break;

			// 체크 해제
			case 4 :
				if (frm.length > 0) {
					for (var i = 0; i < frm.length; i++) {
						frm[i].checked = false;
					}
				} else {
					frm.checked = false;
				}

				break;

			// 체크여부
			default :
				if (frm.length > 0) {
					for (var i = 0; i < frm.length; i++) {
						if (frm[i].checked == true) {
							return true;
						}
					}
				} else {
					if (frm.checked == true) {
						return true;
					}
				}
		}

		return false;
	},

	// 소숫점 자릿수 맞추기
	round : function (num, pos) {
		// kojaepil - 0자리 가능하게 확장
		var posV = Math.pow(10, (pos || pos == 0 ? pos : 2));

		return Math.round(num * posV) / posV;
	},

	// 문자열 반복체크
	isRepetition : function (str, lmt) {
		if (str.length < 1) {
			return false;
		}

		for (var i = 0; i < str.length; i++) {
			var rpt = str.substr(i, 1);
			var key = "";

			for (var j = 0; j < lmt; j++) {
				key += rpt;
			}

			var chk = str.indexOf(key);

			if (chk < 0) {
				continue;
			} else {
				return true;
				break;
			}
		}

		return false;
	},

	// 쿠키값 제어
	cookie : function (name, value, expire) {
		if (typeof value != 'undefined') {
			if (typeof expire != 'undefined') {
				var day = new Date();
				day.setDate(day.getDate() + expire);
				document.cookie = name + "=" + escape(value) + "; path=/; expires=" + day.toGMTString() + ";";
			} else {
				document.cookie = name + "=" + escape(value) + "; path=/;";
			}
		} else {
			var org = document.cookie;
			var dlm = name + "=";
			var x = 0;
			var y = 0;
			var z = 0;

			while (x <= org.length) {
				y = x + dlm.length;

				if (org.substring(x, y) == dlm) {
					if ((z = org.indexOf(";", y)) == -1) {
						z = org.length;
					}

					return unescape(org.substring(y, z));
				}

				x = org.indexOf(" ", x) + 1;

				if (x == 0) {
					break;
				}
			}

			return "";
		}
	},

	// 날짜목록 (년)
	yyList : function (y, s, e) {
		day = new Date();

		if (typeof y == "undefined") {
			var yy = day.getFullYear();
		} else if (y == "") {
			var yy = 0;
		} else {
			var yy = parseInt(y);
		}

		for (var i = (e ? e : day.getFullYear()); i >= (s ? s : 2013); i--) {
			document.write("<option value='" + i + "'" + (i == yy ? " selected" : "") + ">" + i + "년</option>");
		}
	},

	// 날짜목록 (월)
	mmList : function (m) {
		day = new Date();

		if (typeof m == "undefined") {
			var mm = day.getMonth() + 1;
		} else if (m == "") {
			var mm = 0;
		} else {
			var mm = (m.substr(0, 1) == "0") ? parseInt(m.substr(1, m.length)) : parseInt(m);
		}

		for (var i = 1; i <= 12; i++) {
			var n = (i < 10 ? "0" : "") + i;

			document.write("<option value='" + n + "'" + (i == mm ? " selected" : "") + ">" + n + "월</option>");
		}
	},

	// 날짜목록 (일)
	ddList : function (y, m, d) {
		day = new Date();

		if (typeof y == "undefined") {
			var yy = day.getFullYear();
		} else if (y == "") {
			var yy = 0;
		} else {
			var yy = parseInt(y);
		}

		if (typeof m == "undefined") {
			var mm = day.getMonth() + 1;
		} else if (m == "") {
			var mm = 0;
		} else {
			var mm = (m.substr(0, 1) == "0") ? parseInt(m.substr(1, m.length)) : parseInt(m);
		}

		if (typeof d == "undefined") {
			var dd = day.getDate();
		} else if (d == "") {
			var dd = 0;
		} else {
			var dd = (d.substr(0, 1) == "0") ? parseInt(d.substr(1, d.length)) : parseInt(d);
		}

		for (var i = 1; i <= this.endDate(yy, mm); i++) {
			var n = (i < 10 ? "0" : "") + i;

			document.write("<option value='" + n + "'" + (i == dd ? " selected" : "") + ">" + n + "일</option>");
		}
	},

	// 날짜목록 (시)
	hhList : function (h) {
		for (var i = 0; i <= 23; i++) {
			var n = (i < 10 ? "0" : "") + i;

			document.write("<option value='" + n + "'" + (n == h ? " selected" : "") + ">" + n + "시</option>");
		}
	},

	// 날짜목록 (분)
	iiList : function (m) {
		for (var i = 0; i <= 59; i++) {
			var n = (i < 10 ? "0" : "") + i;

			document.write("<option value='" + n + "'" + (n == m ? " selected" : "") + ">" + n + "분</option>");
		}
	},

	// 날짜목록 (초)
	ssList : function (s) {
		for (var i = 0; i <= 59; i++) {
			var n = (i < 10 ? "0" : "") + i;

			document.write("<option value='" + n + "'" + (n == s ? " selected" : "") + ">" + n + "초</option>");
		}
	},

	// 오늘 날짜
	getYmd : function () {
		day = new Date();

		return day.getFullYear() + (day.getMonth() < 9 ? "0" : "") + (day.getMonth() + 1) + (day.getDate() < 10 ? "0" : "") + day.getDate();
	},

	// 윤년 여부
	isLeapYear : function (y) {
		if ((y % 4 == 0 && y % 100 != 0) || (y % 400 == 0 && y % 4000 != 0)) {
			return true;
		} else {
			return false;
		}
	},

	// 해당 월의 마지막 날짜
	endDate : function (y, m) {
		var edate = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];

		if (m == 2) {
			if (this.isLeapYear(y) == true) {
				return 29;
			} else {
				return 28;
			}
		} else {
			return edate[m];
		}
	},

	// 목록 바꾸기
	changeDate : function (sel, y, m) {
		if (typeof y == "undefined" && typeof m == "undefined") {
			for (var i = 1; i <= 12; i++) {
				var n = (i < 10 ? "0" : "") + i;

				sel.options[i] = new Option(n, n);
			}
		} else {
			var ed = this.endDate(parseInt(y), (m.substr(0, 1) == "0") ? parseInt(m.substr(1, m.length)) : parseInt(m));

			for (var i = sel.length - 1; i > 0; i--) {
				sel.options[i] = null;
			}

			for (var i = 1; i <= ed; i++) {
				var n = (i < 10 ? "0" : "") + i;

				sel.options[i] = new Option(n + '일', n);
			}
		}
	},

	isDate : function (sDate) {
		var cDate = sDate.replace(/\-/gi, '');
		
		if (cDate == null || cDate.length != 8) {
			return false;
		}
		
		var sYear = parseInt(cDate.substr(0, 4));
		var sMonth = parseInt(cDate.substr(4, 1) == '0' ? cDate.substr(5, 1) : cDate.substr(4, 2));
		var sDay = parseInt(cDate.substr(6, 1) == '0' ? cDate.substr(7, 1) : cDate.substr(6, 2));
		
		if (sYear < 0 || sMonth <= 0 || sDay <= 0 || sMonth > 12 || sDay > 31) {
			return false;
		} else if (sDay > this.endDate(sYear, sMonth)) {
			return false;
		}

		return true;
	},

	// 좌/우 공백제거
	trim : function (str) {
		return str.replace(/(^\s*)|(\s*$)/gi, "");
	},

	// 배열 섞기
	shuffle : function (arr) {
		var tmp = [];

		for (var i = 0; i < arr.length; i++) {
			tmp[i] = arr[i];
		}

		tmp.sort ( function() { return Math.random() * 2 - 1; } );

		return tmp;
	},

	// 이미지 보정 사이즈
	imgResize : function (ow, oh, mw, mh) {
		var as = [mw, mh];
		var rw, rh;

		if (mw > 0 && mh > 0) {
			if (ow > mw || oh > mh) {
				rw = ow / mw;
				rh = oh / mh;

				if (rw > rh) {
					as[0] = mw;
					as[1] = Math.ceil(oh * mw / ow);
				} else {
					as[0] = Math.ceil(ow * mh / oh);
					as[1] = mh;
				}
			} else {
				as[0] = ow;
				as[1] = oh;
			}
		} else if (mw > 0) {
			if (ow > mw) {
				as[0] = mw;
				rw = mw / ow;
			} else {
				as[0] = ow;
				rw = 1;
			}

			as[1] = Math.ceil(oh * rw);
		} else if (mh > 0) {
			if (oh > mh) {
				as[1] = mh;
				rh = mh / oh;
			} else {
				as[1] = oh;
				rh = 1;
			}

			as[0] = Math.ceil(ow * rh);
		}

		return as;
	},

	// 기본값 설정
	setDefaultValue : function (value, defaultValue) {
		if (!value) {
			return defaultValue;
		}

		return value;
	},

	// 플래쉬 출력
	setFlash : function (s, w, h, param) {
		var doc = '<object classid="clsid:D27CDB6E-AE6D-11cf-96B8-444553540000" codebase="' + location.protocol + '//download.macromedia.com/pub/shockwave/cabs/flash/swflash.cab#version=9,0,0,0" width="' + w + '" height="' + h + '" id="' + (param && param.id ? param.id : Math.floor(Math.random() * 1000000)) + '"' + (param && param.css ? ' css="' + param.css + '"' : '') + (param && param.style ? ' style="' + param.style + '"' : '') + '>'
				+ '<param name="movie" value="' + s + '" />'
				+ '<param name="allowScriptAccess" value="sameDomain" />'
				+ '<param name="allowFullScreen" value="false" />'
				+ '<param name="wmode" value="transparent" />'
				+ '<param name="menu" value="false" />'
				+ '<param name="quality" value="high" />'
				+ '<param name="bgcolor" value="' + (param && param.bgcolor ? param.bgcolor : '#FFFFFF') + '" />'
				+ '<param name="scale" value="exactfit" />'
				+ (param && param.name ? '<param name="' + param.name + '" value="' + param.value + '" />' : '')
				+ '<embed src="' + s + '" allowScriptAccess="sameDomain" allowFullScreen="false" wmode="transparent" menu="false" quality="high" bgcolor="' + (param && param.bgcolor ? param.bgcolor : '#FFFFFF') + '" width="' + w + '" height="' + h + '" ' + (param ? param.name + '="' + param.value + '"' : '') + ' type="application/x-shockwave-flash" pluginspage="' + location.protocol + '//www.macromedia.com/shockwave/download/index.cgi?P1_Prod_Version=ShockwaveFlash"' + (param && param.css ? ' css="' + param.css + '"' : '') + (param && param.style ? ' style="' + param.style + '"' : '') + ' />'
				+ '</object>';

		if (param && param.obj) {
			this.object(param.obj).innerHTML = doc;
		} else {
			document.write(doc);
		}
	},

	// 비디오 출력
	setVideo : function (s, w, h) {
		var temp = s.split("?");

		if (temp[0].match(/\.(swf)$/i)) {
			this.setFlash(s, w, h);
			return;
		}

		var doc = '<object classid="clsid:22D6F312-B0F6-11D0-94AB-0080C74C7E95"'
				+ ' width="' + w + '" height="' + h + '" VIEWASTEXT'
				+ ' codebase="' + location.protocol + '//activex.microsoft.com/activex/controls/mplayer/en/nsmp2inf.cab#Version=5,1,52,701"'
				+ ' standby="Loading Microsoft Windows Media Player components..."'
				+ ' type="application/x-oleobject">\n'
				+ '<param name="AnimationAtStart" value="0" />\n'
				+ '<param name="BufferingTime" value="5" />\n'
				+ '<param name="EnableContextMenu" value="0" />\n'
				+ '<param name="Filename" value="' + s + '" />\n'
				+ '<param name="ShowDisplay" value="0" />\n'
				+ '<param name="ShowPositionControls" value="1" />\n'
				+ '<param name="ShowStatusBar" value="0" />\n'
				+ '<param name="ShowTracker" value="1" />\n'
				+ '<param name="Volume" value="-300" />\n'
				+ '<embed src="' + s + '" width="' + w + '" height="' + h + '" AnimationAtStart="0" BufferingTime="5" EnableContextMenu="0" ShowDisplay="1" ShowPositionControls="1" ShowStatusBar="1" ShowTracker="1" Volume="-300"></embed>\n'
				+ '</object>';

		document.write(doc);
	},

	// 페이지 사이즈
	getPageSize : function () {
		var x, y, w, h;

		if (window.innerHeight && window.scrollMaxY) {
			x = window.innerWidth + window.scrollMaxX;
			y = window.innerHeight + window.scrollMaxY;
		} else if (document.body.scrollHeight > document.body.offsetHeight) {
			x = document.body.scrollWidth;
			y = document.body.scrollHeight;
		} else {
			x = document.body.offsetWidth;
			y = document.body.offsetHeight;
		}

		if (self.innerHeight) {
			if (document.documentElement.clientWidth){
				w = document.documentElement.clientWidth;
			} else {
				w = self.innerWidth;
			}
			h = self.innerHeight;
		} else if (document.documentElement && document.documentElement.clientHeight) {
			w = document.documentElement.clientWidth;
			h = document.documentElement.clientHeight;
		} else if (document.body) {
			w = document.body.clientWidth;
			h = document.body.clientHeight;
		}

		return {width : x < w ? x : w, height : y < h ? h : y};
	},

	// 스크롤 사이즈
	getScrollSize : function (obj) {
		var T, L, W, H;

		with (obj.document) {
			if (obj.document.documentElement && obj.document.documentElement.scrollTop) {
				T = obj.document.documentElement.scrollTop;
				L = obj.document.documentElement.scrollLeft;
			} else if (obj.document.body) {
				T = obj.document.body.scrollTop;
				L = obj.document.body.scrollLeft;
			}

			if (obj.innerWidth) {
				W = obj.innerWidth;
				H = obj.innerHeight;
			} else if (obj.document.documentElement && obj.document.documentElement.clientWidth) {
				W = obj.document.documentElement.clientWidth;
				H = obj.document.documentElement.clientHeight;
			} else {
				W = obj.document.body.offsetWidth;
				H = obj.document.body.offsetHeight
			}
		}

		return {top: T, left: L, width: W, height: H};
	},

	// 문자열 포함 체크
	containsCharsOnly : function (input, chars) {
		for (var i = 0; i < input.length; i++) {
			if (chars.indexOf(input.charAt(i)) == -1) {
				return false;
			}
		}

		return true;
	},

	// 아이디 유효성 체크
	isAvailableID : function (s) {
		if (!/^[a-z0-9]+$/.test(s)) {
			return '영(소)문 및 숫자만 입력이 가능합니다.';
		} else if (s.length < 4 || s.length > 12) {
			return '4자 이상 12자 이하로 입력하세요.';
		}

		return '';
	},
	
	// 주민등록번호 체크
	isJuminNo : function (no) {
		var tot = 0;
		var add = '234567892345';

		if (!/^\d{6}[12340]\d{6}$/.test(no)) {
			return false;
		}

		for (var i = 0; i < 12; i++) {
			tot = tot + parseInt(no.substr(i, 1)) * parseInt(add.substr(i, 1));
		}

		tot = 11 - (tot % 11);

		if (tot == 10) {
			tot = 0;
		} else if (tot == 11) {
			tot = 1;
		}

		if (parseInt(no.substr(12, 1)) != tot) {
			return false;
		}

		return true;
	},

	// 외국인등록번호 체크
	isFgnNo : function (no) {
		var sum = 0;
		var odd = 0;
		var buf = new Array(13);

		if (!/^\d{6}[6789]\d{6}$/.test(no)) {
			return false;
		}

		for (var i = 0; i < 13; i++) {
			buf[i] = parseInt(no.charAt(i));
		}

		odd = buf[7] * 10 + buf[8];

		if(odd % 2 != 0) {
			return false;
		}

		var multipliers = [2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5];

		for(var i = 0; i < 12; i++) {
			sum += (buf[i] *= multipliers[i]);
		}

		sum = 11 - (sum%11);

		if(sum >= 10) {
			sum -= 10;
		}

		sum += 2;

		if(sum >= 10) {
			sum -= 10;
		}

		if (sum != buf[12]) {
			return false;
		}
 
		return true;
	},
	
	// 사업자등록번호 체크
	isBizNo : function (no) { 
		var check = new Array(1, 3, 7, 1, 3, 7, 1, 3, 5, 1);
		var sum = 0;
		var c2;
		var bizNo = no.replace(/-/gi, '');
		var remander;
		
		for (var i = 0; i <= 7; i++) {
			sum += check[i] * bizNo.charAt(i); 
		}
		
		c2 = "0" + (check[8] * bizNo.charAt(8)); 
		c2 = c2.substring(c2.length - 2, c2.length); 
		sum += Math.floor(c2.charAt(0)) + Math.floor(c2.charAt(1)); 
	   
		if (Math.floor(bizNo.charAt(9)) == (10 - (sum % 10)) % 10) {
			return true;
		}
		
		return false; 
	},

	// 폼 체크
	checkForm : function (o) {
		var objBody = o ? o : $$('body')[0];
		var returnValue = '';
		try {
			$('.checkForm').each(function(idx, e) {
				if ($(e).val() != '') {
					return;
				} else if ($(e).attr("disabled") == "disabled") {
					return;
				} else if (!$(e).attr('option')) {
					return;
				}
				
				var op = $(e).attr("option");
				var arr = op.slice(1).slice(0,-1).split(",");
				
				var key;
				var val;
				var v = new Object();
				for(var i=0; i<arr.length; i++) {
					key = arr[i].split(":")[0].trim();
					val = eval(arr[i].split(":")[1].trim());
					v[key] = val;
				}
				
				if (v.sort) {
					v.message = v.sort + '번째 ' + v.message;
				}
				
				/*
				 * [ 사용자함수 추가 ]
				 * isTrueSkip 으로 함수가 할당되고,
				 * 해당 함수가 true 인 경우는 validation 을 진행하지 않고 우회한다.
				 */
				var isTrueSkip = false;
				if (typeof v.isTrueSkip == "function") {
					try {
						isTrueSkip = v.isTrueSkip.apply();
					} catch(e2) {
						returnValue = e2.message;
					}
				}
				
				/*
				 * [ 개별 validate ]
				 * 별도의 option 이전에 먼저 실행한다.
				 * 실행 return 이 있는 경우 returnValue로 할당하여 진행
				 */
				var cValidate = true;
				if (isTrueSkip == false && typeof v.cValidate == "function") {
					try {
						cValidate = v.cValidate.apply();
					} catch(e2) {
						returnValue = e2.message;
					}
					if (typeof cValidate == "undefined") {
						cValidate = true;
					}
					if (cValidate == false) {
						returnValue = "#void#";
					}
				}
				
				if (returnValue == '' && isTrueSkip == false) {
					switch ($(e)[0].tagName.toLowerCase()) {
						case 'input' :
							switch ($(e)[0].type.toLowerCase()) {
								case 'radio' :
									if (v.isMust && !this.radio(document.getElementsByName($(e)[0].name))) {
										returnValue = v.message;
									}
									break;
								case 'checkbox' :
									if (v.isMust && !$(e)[0].checked) {
										returnValue = v.message;
									}
									break;
								default :
									if (v.isMust && $(e)[0].value == '') {
										returnValue = v.message;
									} else if ($(e)[0].value != '') {
										v.message = v.message.replace('입력하세요', '확인하세요');

										if ($(e)[0].getAttribute('maxlength') && parseInt($(e)[0].getAttribute('maxlength')) != $(e)[0].value.length && v.equalLength) {
											switch (v.varType) {
												case 'date' :
													returnValue = v.message + '\n\n세부내용 : 해당 값이 년월일 ' + $(e)[0].getAttribute('maxlength') + '자리가 맞는지 확인하세요.';
													break;
												default :
													returnValue = v.message + '\n\n세부내용 : 해당 값이 ' + $(e)[0].getAttribute('maxlength') + '자리가 맞는지 확인하세요.';
											}
										} else if ($(e)[0].getAttribute('maxlength') && parseInt($(e)[0].getAttribute('maxlength')) < $(e)[0].value.length && !v.equalLength) {
											returnValue = v.message + '\n\n세부내용 : 해당 값이 ' + $(e)[0].getAttribute('maxlength') + '자 이하인지 확인하세요.';
										} else {
											switch (v.varType) {
												case 'number' :
													if (!/^[0-9]+$/.test($(e)[0].value)) {
														returnValue = v.message + '\n\n세부내용 : 숫자만 입력이 가능합니다.';
													}
	
													break;
												case 'float' :
													if (!/^[0-9\.]+$/.test($(e)[0].value)) {
														returnValue = v.message + '\n\n세부내용 : 숫자만 입력이 가능합니다.';
													}
	
													break;
												case 'alnum' :
													if (!/^[a-z0-9]+$/.test($(e)[0].value)) {
														returnValue = v.message + '\n\n세부내용 : 영(소)문 및 숫자만 입력이 가능합니다.';
													}
	
													break;
												case 'ALNUM' :
													if (!/^[A-Z0-9]+$/.test($(e)[0].value)) {
														returnValue = v.message + '\n\n세부내용 : 영(대)문 및 숫자만 입력이 가능합니다.';
													}
	
													break;
												case 'email' :
													if (!this.isAvailableEmail($(e)[0].value)) {
														returnValue = v.message + '\n\n세부내용 : 잘못된 이메일 형식입니다.';
													}
	
													break;
												case 'id' :
													var e = this.isAvailableID($(e)[0].value);
	
													if (e != '') {
														returnValue = v.message + '\n\n세부내용 : ' + e;
													}
	
													break;
												case 'password' :
													if ($(e)[0].value.length < 9) {
														returnValue = v.message + '\n\n세부내용 : 9자 이상으로 입력하세요.';
													} else if (this.isRepetition($(e)[0].value, 3)) {
														returnValue = v.message + '\n\n세부내용 : 같은 문자를 3번 이상 반복할 수 없습니다.';
													} else if (this.containsCharsOnly($(e)[0].value, '1234567890')) {
														returnValue = v.message + '\n\n세부내용 : 영(소)문,숫자,특수문자를 조합하여 사용가능합니다.';
													} else if (this.containsCharsOnly($(e)[0].value, 'abcdefghijklmnopqrstuvwxyz')) {
														returnValue = v.message + '\n\n세부내용 : 영(소)문,숫자,특수문자를 조합하여 사용가능합니다.';
													} else {
														var passPattern = /^((?=.*[a-z]+)(?=.*[!@#$%^&*()\-_=+\\\|\[\]{};:'",.<>\/?]+)(?=.*\d+)).{9,20}$/;
														
														if(!passPattern.test($(e)[0].value)){
															returnValue = v.message + '\n\n세부내용 : 영(소)문,숫자,특수문자를 조합하여 사용가능합니다.';
														}
													}
	
													break;
												case 'url' :
													var check = /(http|https):\/\/(\w+:{0,1}\w*@)?(\S+)(:[0-9]+)?(\/|\/([\w#!:.?+=&%@!\-\/]))?/;
													
													if (!check.test($(e)[0].value)) {
														returnValue = v.message + '\n\n세부내용 : 유효하지 않은 URL 형식입니다.';
													}
													
													break;
												case 'date' :
													if (!this.isDate($(e)[0].value)) {
														returnValue = v.message + '\n\n세부내용 : 유효하지 않은 날짜입니다.';
													}
	
													break;
												case 'bizNo' :
													if (!this.isBizNo($(e)[0].value)) {
														returnValue = v.message + '\n\n세부내용 : 유효하지 않은 사업자등록번호입니다.';
													}
	
													break;
												case 'english' : // 2017.03.13 2016년 정보화 사업 손인수 추가
													var check = /^[A-za-z]/g;
													if (!check.test($(e)[0].value)) {
														returnValue = v.message + '\n\n세부내용 : 영어만 입력이 가능합니다.';
													}
													break;
												case 'chinese' : // 2017.03.13 2016년 정보화 사업 손인수 추가
													var check = /^[A-Z|a-z|0-9]+$/;
													if (check.test($(e)[0].value)) {
														returnValue = v.message + '\n\n세부내용 : 한자 및 한글만 입력이 가능합니다.';
													}
													break;
											}
										}
									} else if (!v.isMust && $(e)[0].value == '' && typeof(v.defaultValue) != 'undefined') { // 2013.11.15 서보람 추가
										$(e)[0].value = v.defaultValue;
									}
							}
	
							break;
						case 'select' :
							if (v.isMust && $(e)[0].options.selectedIndex == 0) {
								returnValue = v.message;
							}
	
							break;
						case 'textarea' :
							if (v.isMust && $(e)[0].value == '') {
								returnValue = v.message;
							} else if ($(e)[0].value != '') {
								v.message = v.message.replace('입력하세요', '확인하세요');

								if ($(e)[0].getAttribute('maxlength') && parseInt($(e)[0].getAttribute('maxlength')) < $(e)[0].value.length) {
									returnValue = v.message + '\n\n세부내용 : 해당 값이 ' + $(e)[0].getAttribute('maxlength') + '자 이하인지 확인하세요.';
								}
							}
	
							break;
					}
				}
				if (returnValue != '') {
					try {
						if (v.focus) {
							$(v.focus).focus();
						} else {
							$(e)[0].focus();
							return false;
						}
					} catch (e) {}
				}
			}.bind(this));
		} catch (e) {
			returnValue = e.message;
		}
		
		return returnValue;
	},
	
	// 나이 계산
	getAge : function (ssn1, ssn2) {
		var age = 0;
		var now = parseInt(this.getYmd().substr(0, 4));
		
		switch (ssn2.substr(0, 1)) {
			case '1' :
			case '2' :
			case '5' :
			case '6' :
				age = now - parseInt("19" + ssn1.substr(0, 2));
				break;
			case '3' :
			case '4' :
			case '7' :
			case '8' :
				age = now - parseInt("20" + ssn1.substr(0, 2));
				break;
			case '9' :
			case '0' :
				age = now - parseInt("18" + ssn1.substr(0, 2));
				break;
		}
		
		if (parseInt(ssn1.substr(2, 4)) > parseInt(this.getYmd().substr(4, 4))) {
			age = age - 1;
		}
		
		return age;
	},
	
	isHash : function(object){
		return object instanceof Hash;
	},
	
	isString : function(object){
		return  $.type(object) === 'string';
	},
	
	isNumber : function(object) {
   		return  $.isNumeric(object);
  	},
	
	isArray : function(object) {
		return $.isArray(object);
	},
	
	isFunction : function(object) {
		return $.isFunction(object);
	},
		
	isUndefined : function(object) {
    	return typeof object === "undefined";
  	},
  
	keys : function(object){
		if($.type(object) !=='Object'){
			throw new TypeError(); 
		}
		
		var results = [];
	    for (var property in object) {
	      if (object.hasOwnProperty(property)) {
	        results.push(property);
	      }
	    }
	    return results;
	},
	//(2024UIUX개선)팝업레이어
	popupEvent: function($id) {
        const $clickBtn = document.activeElement;
        const $header = document.querySelector("#header");
        const $header2 = document.querySelector("#header2");
        const $openTarget = document.querySelector($id);
        const $openTargetType = $openTarget.getAttribute('data-type');
        var focusPopupWrap = $id;
        var focusPopup = $id + " .popup";
        var closeBtn = $id + " .popup-close";
        const $focusPopupWrap = document.querySelector(focusPopupWrap);
        const $focusPopup = document.querySelector(focusPopup);
        const $closeBtn = document.querySelector(closeBtn);

	    // 포커스 트랩을 위한 요소들을 미리 정의
	    const focusableElements = $openTarget.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])');
	    const firstFocusableElement = focusableElements[0];
	    const lastFocusableElement = focusableElements[focusableElements.length - 1];
    
        $openTarget.classList.add("is-open");
        document.body.style.overflow = "hidden";
        //document.body.classList.add("scroll-no");
        //Common.accEvent.open();
        if($openTargetType !== 'bottom' && $header !== null) {
            $header.style.zIndex="1000";
        }
        if($openTargetType !== 'bottom' && $header2 !== null) {
        	if($id == '#mainPopFootLink1' || $id == '#mainPopFootLink2'){
            	$header2.style.display="none";
            	$('body').on('scroll touchmove mousewheel',function(event){
            		event.preventDefault();
            		event.stopPropagation();
            		return false;
            	});
            }
            else if(window.location.pathname == '/main.do'){
            	$('body').on('scroll touchmove mousewheel',function(event){
            		event.preventDefault();
            		event.stopPropagation();
            		return false;
            	});
            }
        }
        if($openTargetType == 'full') {
            $openTarget.setAttribute("tabindex", 0);
            $focusPopupWrap.focus();
        } else {
            $focusPopup.setAttribute("tabindex", 0);
            $focusPopup.focus();
        }
        if($id === '#popTotalSch') {
	        const searchInput = document.getElementById('searchKeyword');
	        if (searchInput) {
	            searchInput.focus();
	        }
	    }

	    // 포커스 트랩 로직 추가
	    $openTarget.addEventListener('keydown', function(e) {
	        const isTabPressed = (e.key === 'Tab' || e.keyCode === 9);
	        if (!isTabPressed) {
	            return;
	        }
	
	        if (e.shiftKey) { // Shift + Tab
	            if (document.activeElement === firstFocusableElement) {
	                lastFocusableElement.focus();
	                e.preventDefault();
	            }
	        } else { // Tab
	            if (document.activeElement === lastFocusableElement) {
	                firstFocusableElement.focus();
	                e.preventDefault();
	            }
	        }
	    });
    
    
        $closeBtn.addEventListener("click", function () {
            $openTarget.classList.remove("is-open");
            $openTarget.classList.add("is-close");
            $focusPopup.removeAttribute("tabindex");
            $clickBtn.focus();
            if ($header !== null){ 
            	$header.style.zIndex="";
            }
            if(window.location.pathname == '/main.do'){
            	$header2.style.display="block";
            	$('body').off('scroll touchmove mousewheel');
            }if($id == '#mainPopFootLink1' || $id == '#mainPopFootLink2'){
            	$header2.style.display="block";
            }
            if(window.location.pathname == '/main.do'){
            	document.body.style.overflow = "hidden";
           	}
           	else{
                document.body.style.overflow = "auto";
            }
            
            //Common.accEvent.close();
            setTimeout(function () {
                $openTarget.classList.remove("is-close");
                //document.body.classList.remove("scroll-no");
                
                if(window.location.pathname == '/main.do'){
            		document.body.style.overflow = "hidden";
           	 	}
           	 	else{
                	document.body.style.overflow = "auto";
                }
            }, 600);
        });
    },
    accEvent: {
    	open : function () {
    		const $container = "#container";
    		const $footer = "#footer";
    		$container.setAttribute("aria-hidden", "true");
    		$footer.setAttribute("aria-hidden", "true");	
	   	},
	   	close : function () {
    		const $container = "#container";
    		const $footer = "#footer";
    		$container.setAttribute("aria-hidden", "false");
    		$footer.setAttribute("aria-hidden", "false");	
	   	}
    }
	
};

//equals java.net.URLEncoder.encode(str, "UTF-8")
function encodeURIComponentEx(str) {
	var returnValue = "";
	var s, u;

	for (var i = 0; i < str.length; i++) {
		s = str.charAt(i);
		u = str.charCodeAt(i);

		if (s == " ") {
			returnValue += "+";
		} else {
			if (u == 0x2a || u == 0x2d || u == 0x2e || u == 0x5f || ((u >= 0x30) && (u <= 0x39)) || ((u >= 0x41) && (u <= 0x5a)) || ((u >= 0x61) && (u <= 0x7a))) {
				returnValue += s;
			} else {
				if ((u >= 0x0) && (u <= 0x7f)) {
					s = "0" + u.toString(16);
					returnValue += "%" + s.substr(s.length - 2);
				} else if (u > 0x1fffff) {
					returnValue += "%" + (oxf0 + ((u & 0x1c0000) >> 18)).toString(16);
					returnValue += "%" + (0x80 + ((u & 0x3f000) >> 12)).toString(16);
					returnValue += "%" + (0x80 + ((u & 0xfc0) >> 6)).toString(16);
					returnValue += "%" + (0x80 + (u & 0x3f)).toString(16);
				} else if (u > 0x7ff) {
					returnValue += "%" + (0xe0 + ((u & 0xf000) >> 12)).toString(16);
					returnValue += "%" + (0x80 + ((u & 0xfc0) >> 6)).toString(16);
					returnValue += "%" + (0x80 + (u & 0x3f)).toString(16);
				} else {
					returnValue += "%" + (0xc0 + ((u & 0x7c0) >> 6)).toString(16);
					returnValue += "%" + (0x80 + (u & 0x3f)).toString(16);
				}
			}
		}
	}

	return returnValue;
}

//equals java.net.URLDecoder.decode(str, "UTF-8")
function decodeURIComponentEx(str) {
	var returnValue = "";
	var s, u, n, f;

	for (var i = 0; i < str.length; i++) {
		s = str.charAt(i);

		if (s == "+") {
			returnValue += " ";
		} else {
			if (s != "%") {
				returnValue += s;
			} else {
				u = 0;
				f = 1;

				while (true) {
					var ss = "";

					for (var j = 0; j < 2; j++) {
						var sss = str.charAt(++i);

						if (((sss >= "0") && (sss <= "9")) || ((sss >= "a") && (sss <= "f"))  || ((sss >= "A") && (sss <= "F"))) {
							ss += sss;
						} else {
							--i;
							break;
						}
					}

					n = parseInt(ss, 16);

					if (n <= 0x7f) { u = n; f = 1; }
					if (n >= 0xc0 && n <= 0xdf) { u = n & 0x1f; f = 2; }
					if (n >= 0xe0 && n <= 0xef) { u = n & 0x0f; f = 3; }
					if (n >= 0xf0 && n <= 0xf7) { u = n & 0x07; f = 4; }
					if (n >= 0x80 && n <= 0xbf) { u = (u << 6) + (n & 0x3f); --f; }

					if (f <= 1) {
						break;
					}

					if (str.charAt(i + 1) == "%") {
						i++;
					} else {
						break;
					}
				}

				returnValue += String.fromCharCode(u);
			}
		}
	}

	return returnValue;
}

// 스크롤 위치 이동
function setScrollPosByClsNm(clsNm){
	var pos = $J(clsNm).offset().top;
	try{
		$J('body, html').animate({
			scrollTop : pos},
			'slow');
	}catch(e){
		
	}
	
//	$J('body').scrollTop(pos);
}


(function($) {
	/**
	* 플레이스홀더.
	* @param callback 콜백함수.
	* @param ... 콜백함수 인자들.
	* @return 값.
	*/
	$.fn.placeholder = function(callback) {
		var $this = $(this);
		var val = $this.val();
		var arg = [];
		
		$this.on("focus", function(e) {
			var $this = $(this);
			if ($this.val() == val) {
				$this.val("");
			}			
		}).on("blur", function(e) {
			var $this = $(this);
			if ($this.val() == "") {
				$this.val(val);
			}
		}).on("keyup", function(e){
			var $this = $(this);
			if (callback && e.keyCode == 13) {
				callback.apply(callback, arg);
			}
		});
		
		if (callback) { // 이벤트 콜백에 인자 넘김
			for (var i = 1; i < arguments.length; i++) {
				arg.push(arguments[i]);
			}
		} else {
			$this.off("keyup"); // 이벤트 해제
		}
		
		return val;
	};
})(jQuery);